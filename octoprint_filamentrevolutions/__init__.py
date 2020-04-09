# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
from octoprint.events import Events
import RPi.GPIO as GPIO
from time import sleep
from flask import jsonify


class FilamentSensorsRevolutions(octoprint.plugin.StartupPlugin,
                                 octoprint.plugin.EventHandlerPlugin,
                                 octoprint.plugin.TemplatePlugin,
                                 octoprint.plugin.SettingsPlugin,
                                 octoprint.plugin.BlueprintPlugin):

    def initialize(self):
        self._logger.info(
            "Running RPi.GPIO version '{0}'".format(GPIO.VERSION))
        if GPIO.VERSION < "0.6":       # Need at least 0.6 for edge detection
            raise Exception("RPi.GPIO must be greater than 0.6")
        GPIO.setwarnings(False)        # Disable GPIO warnings

    @octoprint.plugin.BlueprintPlugin.route("/filament", methods=["GET"])
    def api_get_filament(self):
        status = "-1"
        if self.runout_sensor_enabled():
            status = "0" if self.no_filament() else "1"
        return jsonify(status=status)

    @octoprint.plugin.BlueprintPlugin.route("/jammed", methods=["GET"])
    def api_get_jammed(self):
        status = "-1"
        if self.jam_sensor_enabled():
            status = "1" if self.jammed() else "0"
        return jsonify(status=status)

    @property
    def runout_pin(self):
        return int(self._settings.get(["runout_pin"]))

    @property
    def jam_pin(self):
        return int(self._settings.get(["jam_pin"]))

    @property
    def runout_bounce(self):
        return int(self._settings.get(["runout_bounce"]))

    @property
    def jam_bounce(self):
        return int(self._settings.get(["jam_bounce"]))

    @property
    def runout_switch(self):
        return int(self._settings.get(["runout_switch"]))

    @property
    def jam_switch(self):
        return int(self._settings.get(["jam_switch"]))

    @property
    def mode(self):
        return int(self._settings.get(["mode"]))

    @property
    def no_filament_gcode(self):
        return str(self._settings.get(["no_filament_gcode"])).splitlines()

    @property
    def runout_pause_print(self):
        return self._settings.get_boolean(["runout_pause_print"])

    @property
    def jammed_pause_print(self):
        return self._settings.get_boolean(["jammed_pause_print"])

    @property
    def send_gcode_only_once(self):
        return self._settings.get_boolean(["send_gcode_only_once"])

    def _setup_sensor(self):
        if self.runout_sensor_enabled() or self.jam_sensor_enabled():
            if self.mode == 0:
                self._logger.info("Using Board Mode")
                GPIO.setmode(GPIO.BOARD)
            else:
                self._logger.info("Using BCM Mode")
                GPIO.setmode(GPIO.BCM)

            if self.runout_sensor_enabled():
                self._logger.info(
                    "Filament Runout Sensor active on GPIO Pin [%s]" % self.runout_pin)
                GPIO.setup(self.runout_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            else:
                self._logger.info("Runout Sensor Pin not configured")

            if self.jam_sensor_enabled():
                self._logger.info(
                    "Filament Jam Sensor active on GPIO Pin [%s]" % self.jam_pin)
                GPIO.setup(self.jam_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            else:
                self._logger.info("Jam Sensor Pin not configured")

        else:
            self._logger.info(
                "Pins not configured, won't work unless configured!")

    def on_after_startup(self):
        self._logger.info("Filament Sensors Revolutions started")
        self._setup_sensor()

    def get_settings_defaults(self):
        return dict(
            runout_pin=-1,   # Default is no pin
            runout_bounce=250,  # Debounce 250ms
            runout_switch=0,    # Normally Open
            no_filament_gcode='',
            runout_pause_print=True,

            jam_pin=-1,  # Default is no pin
            jam_bounce=250,  # Debounce 250ms
            jam_switch=1,  # Normally Closed
            jammed_gcode='',
            jammed_pause_print=True,

            mode=0,    # Board Mode
            send_gcode_only_once=False,  # Default set to False for backward compatibility
        )

    def on_settings_save(self, data):
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self._setup_sensor()

    def runout_sensor_triggered(self):
        return self.runout_triggered

    def runout_sensor_enabled(self):
        return self.runout_pin != -1

    def jam_sensor_enabled(self):
        return self.jam_pin != -1

    def no_filament(self):
        return GPIO.input(self.runout_pin) != self.runout_switch

    def jammed(self):
        return GPIO.input(self.jam_pin) != self.jam_switch

    def get_template_configs(self):
        return [dict(type="settings", custom_bindings=False)]

    def on_event(self, event, payload):
        # Early abort in case of out ot filament when start printing, as we
        # can't change with a cold nozzle
        if event is Events.PRINT_STARTED:
            if self.runout_sensor_enabled() and self.no_filament():
                self._logger.info("Printing aborted: no filament detected!")
                self._printer.cancel_print()
            if self.jam_sensor_enabled() and self.jammed():
                self._logger.info("Printing aborted: filament jammed!")
                self._printer.cancel_print()

        # Enable sensor
        if event in (
            Events.PRINT_STARTED,
            Events.PRINT_RESUMED
        ):
            if self.runout_sensor_enabled():
                self._logger.info(
                    "%s: Enabling filament runout sensor." % (event))
                self.runout_triggered = 0  # reset triggered state
                GPIO.remove_event_detect(self.runout_pin)
                GPIO.add_event_detect(
                    self.runout_pin, GPIO.BOTH,
                    callback=self.runout_sensor_callback,
                    bouncetime=self.runout_bounce
                )
            if self.jam_sensor_enabled():
                self._logger.info(
                    "%s: Enabling filament jam sensor." % (event))
                self.jam_triggered = 0  # reset triggered state
                GPIO.remove_event_detect(self.jam_pin)
                GPIO.add_event_detect(
                    self.jam_pin, GPIO.BOTH,
                    callback=self.jam_sensor_callback,
                    bouncetime=self.jam_bounce
                )

        # Disable sensor
        elif event in (
            Events.PRINT_DONE,
            Events.PRINT_FAILED,
            Events.PRINT_CANCELLED,
            Events.ERROR
        ):
            self._logger.info("%s: Disabling filament sensors." % (event))
            if self.runout_sensor_enabled():
                GPIO.remove_event_detect(self.runout_pin)
            if self.jam_sensor_enabled():
                GPIO.remove_event_detect(self.jam_pin)

    def runout_sensor_callback(self, _):
        sleep(self.runout_bounce/1000)

        # If we have previously triggered a state change we are still out
        # of filament. Log it and wait on a print resume or a new print job.
        if self.runout_sensor_triggered():
            self._logger.info("Sensor callback but no trigger state change.")
            return

        if self.no_filament():
            # Set the triggered flag to check next callback
            self.runout_triggered = 1
            self._logger.info("Out of filament!")
            if self.send_gcode_only_once:
                self._logger.info("Sending GCODE only once...")
            else:
                # Need to resend GCODE (old default) so reset trigger
                self.runout_triggered = 0
            if self.runout_pause_print:
                self._logger.info("Pausing print.")
                self._printer.pause_print()
            if self.no_filament_gcode:
                self._logger.info("Sending out of filament GCODE")
                self._printer.commands(self.no_filament_gcode)
        else:
            self._logger.info("Filament detected!")
            if not self.runout_pause_print:
                self.runout_triggered = 0

    def jam_sensor_callback(self, _):
        sleep(self.jam_bounce/1000)

        # If we have previously triggered a state change we are still out
        # of filament. Log it and wait on a print resume or a new print job.
        if self.jam_sensor_triggered():
            self._logger.info("Sensor callback but no trigger state change.")
            return

        if self.jammed():
            # Set the triggered flag to check next callback
            self.jam_triggered = 1
            self._logger.info("Filament jammed!")
            if self.send_gcode_only_once:
                self._logger.info("Sending GCODE only once...")
            else:
                # Need to resend GCODE (old default) so reset trigger
                self.jam_triggered = 0
            if self.jammed_pause_print:
                self._logger.info("Pausing print.")
                self._printer.pause_print()
            if self.jammed_gcode:
                self._logger.info("Sending jammed GCODE")
                self._printer.commands(self.jammed_gcode)
        else:
            self._logger.info("Filament not jammed!")
            if not self.jammed_pause_print:
                self.jam_triggered = 0

    def get_update_information(self):
        return dict(
            filamentrevolutions=dict(
                displayName="Filament Sensors Revolutions",
                displayVersion=self._plugin_version,

                # version check: github repository
                type="github_release",
                user="RomRider",
                repo="Octoprint-Filament-Revolutions",
                current=self._plugin_version,

                # update method: pip
                pip="https://github.com/RomRider/Octoprint-Filament-Revolutions/archive/{target_version}.zip"
            )
        )


__plugin_name__ = "Filament Sensors Revolutions"
__plugin_version__ = "1.0.0"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = FilamentSensorsRevolutions()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }


def __plugin_check__():
    try:
        import RPi.GPIO
    except ImportError:
        return False

    return True
