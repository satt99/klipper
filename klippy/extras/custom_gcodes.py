# Extended Gcode Commands
#
# Copyright (C) 2018  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

class CustomGcode:
    def __init__(self, config):
        self.supported_gcodes = {
            'LOAD_FILAMENT':True,
            'UNLOAD_FILAMENT':True,
            'SET_BEEPER':True}
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        gcodes = config.get('enabled_gcodes', None)
        if gcodes is not None:
            gcodes = gcodes.split('\n')
            for gc in gcodes:
                gc = gc.strip()
                if gc and gc in self.supported_gcodes:
                    self.supported_gcodes[gc] = True
        self.display = None
        self.config_extruder_accel = None
        self.gcode = self.printer.lookup_object('gcode')
        for key in self.supported_gcodes:
            if self.supported_gcodes[key]:
                try:
                    command_func = getattr(self, "cmd_" + key)
                    help_attr = getattr(self, "cmd_" + key + "_help")
                except:
                    raise config.error("Gcode [%s] Not supported" % (key))
                self.gcode.register_command(key, command_func, 
                                            desc = help_attr)
                logging.info("Extended gcode " + key + " enabled" )
        self.beeper_off_timer = self.reactor.register_timer(self._beeper_off)
    def printer_state(self, state):
        if state == 'ready':
            try:
                self.display = self.printer.lookup_object('display')
            except:
                self.display = None
                self.gcode.respond_info("Display not added to config")
    def _beeper_off(self, eventtime):
        self.gcode.run_script_from_command("SET_PIN PIN=beeper VALUE=0")
        return self.reactor.NEVER
    cmd_SET_BEEPER_help = "Toggle beeper on for provided duration (default 1s)"
    def cmd_SET_BEEPER(self, params):
        duration = self.gcode.get_float('DURATION', params, 1., above=.1)
        self.gcode.run_script_from_command("SET_PIN PIN=beeper VALUE=1")
        waketime = self.reactor.monotonic() + duration
        self.reactor.update_timer(self.beeper_off_timer, waketime)
    cmd_LOAD_FILAMENT_help = "Load filament into Extruder"
    def cmd_LOAD_FILAMENT(self, params):
        length = self.gcode.get_float('LENGTH', params, 95., above=25.)
        length -= 25.
        first_extrude = "G1 E%.2f F400" % (length)
        toolhead = self.printer.lookup_object('toolhead')
        if self.display:
            self.display.set_message("Loading Filament...")
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command("G1 E0.0")
        self.gcode.run_script_from_command(first_extrude)
        self.gcode.run_script_from_command("G1 E25 F100")
        toolhead.wait_moves()
        if self.display:
            self.display.set_message("Load Complete", 5.)
    cmd_UNLOAD_FILAMENT_help = "Unload filament from Extruder"
    def cmd_UNLOAD_FILAMENT(self, params):
        length = self.gcode.get_float('LENGTH', params, 80., above=45.)
        length = -1 * (length - 45.)
        second_extrude = "G1 E%.2f F1000" % (length)
        toolhead = self.printer.lookup_object('toolhead')
        if self.display:
            self.display.set_message("Unloading Filament...")
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command("G1 E0.0")
        self.gcode.run_script_from_command("G1 E-45 F5200")
        self.gcode.run_script_from_command(second_extrude)
        toolhead.wait_moves()
        toolhead.motor_off()
        if self.display:
            self.display.set_message("REMOVE FILAMENT NOW!", 5.)
        self.gcode.run_script_from_command("SET_BEEPER DURATION=1")
    
def load_config(config):
    return CustomGcode(config)