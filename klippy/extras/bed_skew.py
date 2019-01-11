# Bed Skew Correction
#
# This implementation is a port of Marlin's skew correction as
# implemented in planner.h, Copyright (C) Marlin Firmware
#
# https://github.com/MarlinFirmware/Marlin/tree/1.1.x/Marlin
#
# Copyright (C) 2018  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.


class BedSkew:
    SUPPORTED_KINEMATICS = {'cartesian', 'corexy'}
    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None
        kin_type = config.getsection('printer').get('kinematics')
        if kin_type not in self.SUPPORTED_KINEMATICS:
            raise config.error(
                "bed_skew: unsupported kinematics '%s'"
                % kin_type)
        self.xy_factor = config.getfloat('xy_skew_factor', 0.)
        self.xz_factor = config.getfloat('xz_skew_factor', 0.)
        self.yz_factor = config.getfloat('yz_skew_factor', 0.)
        x_config = config.getsection('stepper_x')
        self.x_min = x_config.getfloat('position_min', 0.)
        self.x_max = x_config.getfloat('position_max')
        y_config = config.getsection('stepper_y')
        self.y_min = y_config.getfloat('position_min', 0.)
        self.y_max = y_config.getfloat('position_max')
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
        gcode = self.printer.lookup_object('gcode')
        gcode.set_move_transform(self)
        self.downstream_transform = None
    def handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
    def _check_pos_range(self, pos, min_offset=0):
        return ((self.x_min + min_offset) <= pos[0] <= self.x_max) and \
               ((self.y_min + min_offset)) <= pos[1] < self.y_max
    def set_downstream_transform(self, transform):
        self.downstream_transform = transform
    def calc_skew(self, pos):
        if self._check_pos_range(pos, 1):
            skewed_x = pos[0] - pos[1] * self.xy_factor \
                - pos[2] * (self.xz_factor - (self.xy_factor * self.yz_factor))
            skewed_y = pos[1] - pos[2] * self.yz_factor
            new_pos = [skewed_x, skewed_y, pos[2], pos[3]]
            if self._check_pos_range(new_pos):
                return new_pos
        return pos
    def calc_unskew(self, pos):
        if self._check_pos_range(pos):
            skewed_x = pos[0] + pos[1] * self.xy_factor \
                + pos[2] * self.xz_factor
            skewed_y = pos[1] + pos[2] * self.yz_factor
            new_pos = [skewed_x, skewed_y, pos[2], pos[3]]
            if self._check_pos_range(new_pos):
                return new_pos
        return pos
    def get_position(self):
        if self.downstream_transform is None:
            cur_pos = self.toolhead.get_position()
            u_pos = self.calc_unskew(cur_pos)
        else:
            cur_pos = self.downstream_transform.get_position()
            u_pos = self.calc_unskew(cur_pos)
            self.downstream_transform.update_position(u_pos)
        return u_pos
    def move(self, newpos, speed):
        next_pos = self.calc_skew(newpos)
        if self.downstream_transform is None:
            self.toolhead.move(next_pos, speed)
        else:
            self.downstream_transform.move(next_pos, speed)

def load_config(config):
    return BedSkew(config)
