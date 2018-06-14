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
            'M117':False,
            'M204':False}
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        gcodes = config.get('enabled_gcodes', None).split('\n')
        if gcodes is not None:
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
            if self.supported_gcodes['M204'] and (self.config_extruder_accel == None):
                extruder = self.printer.lookup_object('extruder0')
                self.config_extruder_accel = extruder.max_e_accel
            if self.supported_gcodes['M117']:
                try:
                    self.display = self.printer.lookup_object('display')
                except:
                    self.display = None
                    self.gcode.respond_info("Display not added to config")
    cmd_M204_help = "Set printer acceleration limit"
    def cmd_M204(self, params):
        toolhead = self.printer.lookup_object('toolhead')
        print_time = toolhead.get_last_move_time()
        # Check for "old style" Marlin pre-2015 change to Printing Acceleration
        max_accel = self.gcode.get_float('S', params, None, above=0.,
                                         maxval=toolhead.config_max_accel)
        # Check new Style Marlin M204 (P/R/T)
        if not max_accel:
            max_accel = self.gcode.get_float('P', params, None, above=0.,
                                              maxval=toolhead.config_max_accel)
        # If M204 did not include acceleration for a print move, check for
        # a Travel move
        if not max_accel:
            max_accel = self.gcode.get_float('T', params, None, above=0., 
                                             maxval=toolhead.config_max_accel)
        max_retract_accel = self.gcode.get_float('R', params, None, above=0.,
                                                 maxval=self.config_extruder_accel)
        if max_accel:
            toolhead.max_accel = max_accel
            # when changing acceleration, use Klipper's default for accel_to_decel
            toolhead.max_accel_to_decel = .5 * toolhead.max_accel
        extruder = self.printer.lookup_object('extruder0')
        if max_retract_accel:    
            extruder.max_e_accel = max_retract_accel
        self.gcode.respond_info("M204 Set Max Accel: Print: %.6f, Retract: %.6f, Travel: %.6f" 
                            % (toolhead.max_accel, extruder.max_e_accel, toolhead.max_accel))
    cmd_M117_help = "Show Message on Display"
    def cmd_M117(self, params):
        if self.display and '#original' in params:
            msg = params['#original']
            if len(msg) > 5:
                msg = msg[5:]
                self.display.set_message(msg)
            else:
                self. display.set_message(None)
    def _beeper_off(self, eventtime):
        self.gcode.run_script("SET_PIN PIN=beeper VALUE=0")
        return self.reactor.NEVER
    cmd_LOAD_FILAMENT_help = "Load filament into Extruder"
    def cmd_LOAD_FILAMENT(self, params):
        length = 95.
        if 'LENGTH' in params:
            length = self.gcode.get_float('LENGTH', params, 80, above=25.)
        length -= 25.
        first_extrude = "G1 E%.2f F400" % (length)
        toolhead = self.printer.lookup_object('toolhead')
        if self.display:
            self.display.set_message("Loading Filament...")
        self.gcode.run_script("M83")
        self.gcode.run_script("G1 E0.0")
        self.gcode.run_script(first_extrude)
        self.gcode.run_script("G1 E25 F100")
        toolhead.wait_moves()
        if self.display:
            self.display.set_message("Load Complete", 5.)
    cmd_UNLOAD_FILAMENT_help = "Unload filament from Extruder"
    def cmd_UNLOAD_FILAMENT(self, params):
        length = 80.
        if 'LENGTH' in params:
            length = self.gcode.get_float('LENGTH', params, 80, above=45.)
        length = -1 * (length - 45.)
        second_extrude = "G1 E%.2f F1000" % (length)
        toolhead = self.printer.lookup_object('toolhead')
        if self.display:
            self.display.set_message("Unloading Filament...")
        self.gcode.run_script("M83")
        self.gcode.run_script("G1 E0.0")
        self.gcode.run_script("G1 E-45 F5200")
        self.gcode.run_script(second_extrude)
        toolhead.wait_moves()
        toolhead.motor_off()
        if self.display:
            self.display.set_message("REMOVE FILAMENT NOW!", 5.)
        self.gcode.run_script("SET_PIN PIN=beeper VALUE=1")
        waketime = self.reactor.monotonic() + .5
        self.reactor.update_timer(self.beeper_off_timer, waketime)
    # TODO: add gcodes for TOGGLE_BEEPER and perhaps TONE_BEEPER if I switch to pwm
    

def load_config(config):
    return CustomGcode(config)