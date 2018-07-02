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

class ProbeTemp:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.display = None
        self.cal_helper = ProbeCalibrationHelper(config, self)
        self.sensor_type = config.get('sensor_type', None)
        if self.sensor_type is None:
            raise self.config.error("ProbeTemp: sensor_type is a required field")
        self.lock = threading.Lock()
        self.probe_offsets = None
        offsets = self.config.get('t_offsets', None)
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
        self.sensor = None
        self.sensor_temp = 0.
        self.z_offset = 0.
        if self.sensor_type in thermistor.Sensors:
            params = thermistor.Sensors[self.sensor_type]
            self.sensor = thermistor.Thermistor(config, params)
            self.sensor.setup_minmax(0., 100.)
            self.sensor.setup_callback(self.temperature_callback)
        self.gcode.register_command(
            'GET_PROBE_TEMP', self.cmd_GET_PROBE_TEMP, desc=self.cmd_GET_PROBE_TEMP_help)
        self.gcode.register_command(
            'PROBE_WAIT', self.cmd_PROBE_WAIT, desc=self.cmd_PROBE_WAIT_help)
    def printer_state(self, state):
        if state == 'ready':
            self.cal_helper.printer_state(state)
            self.toolhead = self.printer.lookup_object('toolhead')
            if self.sensor is None:
                # A sensor was added to config but not found in the default sensor dictoinary.
                # Check to see if it is a custom thermistor.
                custom_thermistor = self.printer.lookup_object(self.sensor_type)
                self.sensor = custom_thermistor.create(self.config)
                if self.sensor:
                    self.sensor.setup_minmax(0., 100.)
                    self.sensor.setup_callback(self.temperature_callback)
    def temperature_callback(self, readtime, temp):
        with self.lock:
            self.sensor_temp = temp
    def get_current_temp(self):
        with self.lock:
            return self.sensor_temp
    def get_probe_offset(self):
        offset_temp = self.get_current_temp()
        if self.probe_offsets:
            last_idx = len(self.probe_offsets) - 1
            if offset_temp <= self.probe_offsets[0][0]:
                # Don't attempt to interpolate above or below
                return 0.
            elif offset_temp >= self.probe_offsets[last_idx][0]:
                return self.probe_offsets[last_idx][1]
            else:
                # Interpolate between points, not over the entire curve, because the
                # change is not linear across all temperatures
                for index in range(last_idx):
                    if offset_temp > self.probe_offsets[index][0] and \
                       offset_temp <= self.probe_offsets[index+1][0]:
                        temp_delta = self.probe_offsets[index+1][0] - self.probe_offsets[index][0]
                        t = (offset_temp - self.probe_offsets[index][0]) / (temp_delta)
                        return (1. - t) * self.probe_offsets[index][1] + \
                               t * self.probe_offsets[index+1][1]
            self.gcode.respond_error("probe_temp: unable to retrieve offset from probe temperature")
            return 0.
        else:
            return 0.
    def set_z_adjustment(self):
        new_offset =  self.get_probe_offset()
        z_adj = self.z_offset - new_offset
        self.z_offset = new_offset
        self.gcode.run_script_from_command("SET_GCODE_OFFSET Z_ADJUST=%.4f" % (z_adj))
    def pause_for_temp(self, target_temp, timeout=300, heat_up=True):
        total_time = 0
        if heat_up:
            while self.get_current_temp() <= target_temp:
                self.pause_for_time(1)
                total_time += 1
                if timeout and total_time >= timeout:
                    return False
        else:
            while self.get_current_temp() >= target_temp:
                self.pause_for_time(1)
                total_time += 1
                if timeout and total_time >= timeout:
                    return False
        return True
    def pause_for_time(self, dwell_time):
        for i in range(dwell_time):
            self.toolhead.dwell(1.)
            self.toolhead.wait_moves()
            self.gcode.respond("Probe Temp: %.2f" % (self.get_current_temp()))
    def _get_heater_status(self):
        extruder = self.toolhead.get_extruder().get_heater()
        bed = self.printer.lookup_object('heater_bed')
        reactor = self.printer.get_reactor()
        eventtime = reactor.monotonic()
        e_status = extruder.get_status(eventtime)
        b_status = bed.get_status(eventtime)
        return e_status['target'] > 0., b_status['target'] > 0.
    cmd_GET_PROBE_TEMP_help = "Return the probe temperature if it has a thermistor"
    def cmd_GET_PROBE_TEMP(self, params):
        self.gcode.respond_info("Probe Temperature: %.2f" % (self.get_current_temp()))
    cmd_PROBE_WAIT_help = "Pause until the probe thermistor reaches a temperature"
    def cmd_PROBE_WAIT(self, params):
        extr_on, bed_on = self._get_heater_status()
        wait_temp = self.gcode.get_float('TEMP', params, 35., minval=25., maxval=65.)
        timeout = self.gcode.get_int('TIMEOUT', params, 0, minval=0) * 60
        direction = self.gcode.get_str('DIRECTION', params, 'up').lower()
        temp_acheived = True
        if direction == 'down' or direction == 'cool':
            if extr_on or bed_on:
                # Heaters are on, we can't wait
                self.gcode.respond_info("Heaters are on, please disable "
                                        "before attempting to wait for probe to cool.")
                return
            temp_acheived = self.pause_for_temp(wait_temp, timeout, False)
        elif direction == 'up' or direction == 'heat':
            if not extr_on and not bed_on:
                # Heaters are off, we can't wait
                self.gcode.respond_info("Heaters are off, please enable "
                                        "before attempting to wait for probe to heat.")
                return
            temp_acheived = self.pause_for_temp(wait_temp, timeout)
        else:
            return
        if temp_acheived:
            self.set_z_adjustment()

#TODO: It will soon be possible
class ProbeCalibrationHelper:
    def __init__(self, config, probetemp):
        self.sensor = probetemp
        self.printer = self.sensor.printer
        self.gcode = self.sensor.gcode
        self.display = None
        stepper_config = config.getsection('stepper_z')
        self.z_offset = stepper_config.getfloat('position_endstop')
        self.gcode.register_command(
            'CALIBRATE_PROBE_TEMP', self.cmd_CALIBRATE_PROBE_TEMP, 
            desc=self.cmd_CALIBRATE_PROBE_TEMP_help)
    def printer_state(self, state):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.kinematics = self.toolhead.get_kinematics()
        try:
            self.display = self.printer.lookup_object('display')
        except:
            # Display not available.  Its not necessary, only used for feedback
            self.display = None
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
    def cmd_CALIBRATE_PROBE_TEMP(self, params):
        reactor = self.printer.get_reactor()
        xstart = self.gcode.get_float('X', params, 97., minval=0., maxval=245.)
        ystart = self.gcode.get_float('Y', params, 103, minval=0, maxval=210.)
        max_probe_temp = self.gcode.get_float('TARGET', params, 45., above=25.)
        bed_temp = self.gcode.get_float('B_TMP', params, 70., above=50.)
        extruder_temp = self.gcode.get_float('E_TMP', params, 200., above=0.)
        timeout = self.gcode.get_int('TIMEOUT', params, 180, minval=0)
        if extruder_temp < 170.0:
            extruder_temp = None
        z_pos = 0.
        probe_array = []
        self.gcode.respond_info("Starting Probe Temperature Calibration...")
        if self.display:
            self.display.set_message("PINDA Cal Start...")
        self.gcode.run_script_from_command("G28")
        self.gcode.run_script_from_command("G1 X%.2f Y%.2f Z150 F5000" % (xstart, ystart))
        self.gcode.run_script_from_command("M190 S%.2f" % (bed_temp))
        if extruder_temp:
            self.gcode.run_script_from_command("M109 S%.2f" % (extruder_temp))
        self.gcode.run_script_from_command("G28 Z0")
        # loop probes until max_probe temp is reach
        keep_alive = True
        start_time = reactor.monotonic()
        current_temp = self.sensor.get_current_temp()
        while current_temp < max_probe_temp and keep_alive: 
            z_pos = self._next_probe()
            # store temp, offset, and time
            probe_array.append((current_temp, z_pos - self.z_offset, reactor.monotonic() - start_time))
            self.gcode.respond("Probe Temp: %.2f, Z-Position: %.4f" % 
                              (current_temp, z_pos))
            if self.display:
                self.display.set_message("P: %.2f, Z: %.2f" % 
                                        (current_temp, z_pos), 5.)
            # Lower Head to absorb maximum heat
            self._move_toolhead_z(.2)
            keep_alive = self.sensor.pause_for_temp(min(current_temp + .5, max_probe_temp), 
                                                    timeout=timeout)
            current_temp = self.sensor.get_current_temp()                                 
        self.gcode.respond_info("Probe Calibration Complete!")
        if self.display:
            self.display.set_message("PINDA Cal Done!", 10.)
        # turn off temps, raise Z
        self.gcode.run_script_from_command("M104 S0")
        self.gcode.run_script_from_command("M140 S0")
        self.gcode.run_script_from_command("G1 Z50")
        
        # Save info to dictionary to file
        try:
            f = open(HOME_DIR + "/PindaTemps.json", "wb")
        except:
            f = None
            self.gcode.respond_info("Unable to open file to dump json serialized dict")
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
