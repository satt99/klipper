# Probe Temp Compensation Support
#
# Copyright (C) 2018  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, math, threading
import thermistor
import json

from os.path import expanduser
HOME_DIR = expanduser('~')

Z_LIFT = 5.
Z_SPEED = 10.
TIMEOUT = 180

# Linear interpolation between two values
def lerp(t, v0, v1):
    return (1. - t) * v0 + t * v1

class ProbeTemp:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.cal_helper = ProbeCalibrationHelper(config, self)
        self.sensor_type = config.get('sensor_type', None)
        if self.sensor_type is None:
            raise config.error("ProbeTemp: sensor_type is a required field")
        self.lock = threading.Lock()
        self.probe_offsets = None
        offsets = config.get('t_offsets', None)
        if offsets:
            offsets = offsets.split('\n')
            try:
                offsets = [line.split(',', 1) for line in offsets if line.strip()]
                self.probe_offsets = [(float(p[0].strip()), float(p[1].strip()))
                                      for p in offsets]
            except:
                raise config.error("Unable to parse probe offsets in %s" % (
                    config.get_name()))
            if len(self.probe_offsets) < 2:
                raise config.error("Need at least 2 points for %s" % (
                    config.get_name()))
        logging.info("Probe offsets generated:")
        for temp, offset in self.probe_offsets:
            logging.info("(%.2fC,%.4f)" % (temp, offset))
        self.sensor_temp = 0.
        self.target = 0.
        self.offset_applied = 0.
        if config.has_section("thermistor " + self.sensor_type):
            self.printer.load_object(
                config, "thermistor " + self.sensor_type)
        pheaters = self.printer.load_object(config, 'heaters')
        self.sensor = pheaters.setup_sensor(config)
        mintemp = config.getfloat('min_temp', 0.)
        maxtemp = config.getfloat('max_temp', 100.)
        self.sensor.setup_minmax(mintemp, maxtemp)
        self.sensor.setup_callback(self.temperature_callback)
        pheaters.register_sensor(config, self, "P")
        self.printer.register_event_handler("klippy:ready",
                                            self.handle_ready)
        self.gcode.register_command(
            'GET_PROBE_TEMP', self.cmd_GET_PROBE_TEMP,
            desc=self.cmd_GET_PROBE_TEMP_help)
        self.gcode.register_command(
            'PROBE_WAIT', self.cmd_PROBE_WAIT,
            desc=self.cmd_PROBE_WAIT_help)
        self.gcode.register_command(
            'APPLY_TEMP_OFFSET', self.cmd_APPLY_TEMP_OFFSET,
            desc=self.cmd_APPLY_TEMP_OFFSET_help)
    def handle_ready(self):
        self.cal_helper.handle_ready()
        self.toolhead = self.printer.lookup_object('toolhead')
    def temperature_callback(self, readtime, temp):
        with self.lock:
            self.sensor_temp = temp
    def get_temp(self, eventtime):
        with self.lock:
            return self.sensor_temp, self.target
    def get_probe_offset(self):
        offset_temp = self.get_temp(0)[0]
        if self.probe_offsets:
            last_idx = len(self.probe_offsets) - 1
            if offset_temp <= self.probe_offsets[0][0]:
                # Clamp offsets below minimum temperature to 0
                return 0.
            elif offset_temp >= self.probe_offsets[last_idx][0]:
                return self.probe_offsets[last_idx][1]
            else:
                # Interpolate between points, not over the entire curve,
                # because the change is not linear across all temperatures
                for index in range(last_idx):
                    if offset_temp > self.probe_offsets[index][0] and \
                       offset_temp <= self.probe_offsets[index+1][0]:
                        temp_delta = self.probe_offsets[index+1][0] \
                            - self.probe_offsets[index][0]
                        t = (offset_temp - self.probe_offsets[index][0]) \
                            / (temp_delta)
                        offset = lerp(t, self.probe_offsets[index][1],
                                      self.probe_offsets[index+1][1])
                        self.gcode.respond_info(
                            "[probe_temp] Current Temp: %.2f, Calculated Offset: %.4f"
                            % (offset_temp, offset))
                        return offset
            self.gcode.respond_error(
                "probe_temp: unable to retrieve offset from probe temperature")
            return 0.
        else:
            return 0.
    def set_z_adjustment(self):
        new_offset = self.get_probe_offset()
        z_adj = new_offset - self.offset_applied
        self.offset_applied = new_offset
        self.gcode.run_script_from_command(
            "SET_GCODE_OFFSET Z_ADJUST=%.4f" % (z_adj))
    def pause_for_temp(self, timeout=300, compare=lambda x, y: x <= y):
        total_time = 0
        temp, target = self.get_temp(0)
        while compare(temp, target):
            total_time += 1
            if timeout:
                remaining = timeout - total_time
                self.pause_for_time(1, remaining)
                if remaining <= 0:
                    return False
            else:
                self.pause_for_time(1)
            temp, target = self.get_temp(0)
        return True
    def pause_for_time(self, dwell_time, time_remaining=None):
        for i in range(dwell_time):
            self.toolhead.dwell(1.)
            self.toolhead.wait_moves()
            msg = "Probe Temp: %.2f/%.2f" % (self.get_temp(0))
            if time_remaining is not None:
                msg += " Time Remaining: %d seconds" % (time_remaining)
            self.gcode.respond_info(msg)
    def _get_heater_status(self):
        extruder = self.toolhead.get_extruder().get_heater()
        bed = self.printer.lookup_object('heater_bed')
        reactor = self.printer.get_reactor()
        eventtime = reactor.monotonic()
        e_status = extruder.get_status(eventtime)
        b_status = bed.get_status(eventtime)
        return e_status['target'] > 0., b_status['target'] > 0.
    cmd_GET_PROBE_TEMP_help = "Return the probe temperature"
    def cmd_GET_PROBE_TEMP(self, gcmd):
        gcmd.respond_info(
            "Probe Temperature: %.2f/%.2f" % (self.get_temp(0)))
    cmd_PROBE_WAIT_help = "Pause until the probe reaches a temperature"
    def cmd_PROBE_WAIT(self, gcmd):
        extr_on, bed_on = self._get_heater_status()
        wait_temp = gcmd.get_float(
            'TEMP', 35., minval=20., maxval=70.)
        timeout = gcmd.get_int('TIMEOUT', 0, minval=0)
        self.target = wait_temp
        # direction = self.gcode.get_str('DIRECTION', params, 'up').lower()
        probe_cooling = not extr_on and not bed_on
        if probe_cooling:
            self.gcode.respond_info("Waiting for probe to cool...")
            temp_acheived = self.pause_for_temp(
                timeout=timeout, compare=lambda x, y: x >= y)
        else:
            self.gcode.respond_info("Waiting for probe to heat...")
            temp_acheived = self.pause_for_temp(timeout=timeout)
        if temp_acheived:
            self.gcode.respond_info("Pinda Temp Achieved")
        else:
            self.gcode.respond_info("Wait for Pinda Temp Timed Out")
        self.target = 0.
    cmd_APPLY_TEMP_OFFSET_help = "Apply a gcode offset based on the probe's temperature"
    def cmd_APPLY_TEMP_OFFSET(self, gcmd):
        self.set_z_adjustment()


class ProbeCalibrationHelper:
    def __init__(self, config, probetemp):
        self.sensor = probetemp
        self.printer = self.sensor.printer
        self.gcode = self.sensor.gcode
        probe_config = config.getsection('probe')
        self.z_offset = probe_config.getfloat('z_offset')
        self.gcode.register_command(
            'CALIBRATE_PROBE_TEMP', self.cmd_CALIBRATE_PROBE_TEMP,
            desc=self.cmd_CALIBRATE_PROBE_TEMP_help)
    def handle_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.kinematics = self.toolhead.get_kinematics()
    def _next_probe(self):
        self._move_toolhead_z(Z_LIFT)
        self.gcode.run_script_from_command("PROBE")
        self.toolhead.wait_moves()
        z_pos = self.kinematics.calc_position()[2]
        return z_pos
    def _move_toolhead_z(self, z_pos, relative=False):
        current_pos = self.toolhead.get_position()
        if relative:
            current_pos[2] += z_pos
        else:
            current_pos[2] = z_pos
        self.toolhead.move(current_pos, Z_SPEED)
    def _start_calibration(self):
        pass
    cmd_CALIBRATE_PROBE_TEMP_help = "Calbrate the probe's offset based on its temperature"
    def cmd_CALIBRATE_PROBE_TEMP(self, gcmd):
        reactor = self.printer.get_reactor()
        xstart = gcmd.get_float('X', 97., minval=0., maxval=245.)
        ystart = gcmd.get_float('Y', 103, minval=0, maxval=210.)
        max_probe_temp = gcmd.get_float('TARGET', 45., above=25.)
        bed_temp = gcmd.get_float('B_TMP', 70., minval=0.)
        extruder_temp = gcmd.get_float('E_TMP', 200., minval=0.)
        timeout = gcmd.get_int('TIMEOUT', 180, minval=0)
        if extruder_temp < 170.0:
            extruder_temp = None
        z_pos = 0.
        probe_array = []
        gcmd.respond_info("Starting Probe Temperature Calibration...")
        self.gcode.run_script_from_command("M190 S%.2f" % (bed_temp))
        if extruder_temp:
            self.gcode.run_script_from_command("M109 S%.2f" % (extruder_temp))
        self.gcode.run_script_from_command("G28")
        self.gcode.run_script_from_command(
            "G1 X%.2f Y%.2f Z%.2f F5000" % (xstart, ystart, Z_LIFT))
        # loop probes until max_probe temp is reach
        keep_alive = True
        start_time = reactor.monotonic()
        current_temp = self.sensor.get_temp(0)[0]
        while current_temp < max_probe_temp and keep_alive:
            z_pos = self._next_probe()
            # store temp, offset, and time
            probe_array.append(
                (current_temp, z_pos - self.z_offset,
                 reactor.monotonic() - start_time))
            gcmd.respond_info(
                "Probe Temp: %.2f, Z-Position: %.4f" % (current_temp, z_pos))
            # Lower Head to absorb maximum heat
            self._move_toolhead_z(.2)
            keep_alive = self.sensor.pause_for_temp(
                min(current_temp + .5, max_probe_temp), timeout=timeout)
            current_temp = self.sensor.get_temp(0)[0]
        gcmd.respond_info("Probe Calibration Complete!")
        # turn off temps, raise Z
        self.gcode.run_script_from_command("M104 S0")
        self.gcode.run_script_from_command("M140 S0")
        self.gcode.run_script_from_command("G1 Z50")

        # Save info to dictionary to file
        try:
            f = open(HOME_DIR + "/PindaTemps.json", "wb")
        except:
            f = None
            gcmd.respond_info(
                "Unable to open file to dump json serialized dict")
        if f:
            out_dict = {
                'X': xstart,
                'Y': ystart,
                'EXTRUDER': extruder_temp,
                'BED': bed_temp,
                'TIMED_OUT': keep_alive,
                'PROBE_VALS': probe_array}
            json.dump(out_dict, f)
            f.close()
        # Wait for Z to raize and turn off motors
        self.toolhead.wait_moves()
        self.toolhead.motor_off()

def load_config(config):
    return ProbeTemp(config)
