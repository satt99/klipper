// Fan tachometer support
//
// Copyright (C) 2019  Eric Callahan <arksine.code@gmail.com>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "basecmd.h" // oid_alloc
#include "board/gpio.h" // struct gpio_irq
#include "board/irq.h" // irq_disable
#include "command.h" // DECL_COMMAND
#include "sched.h" // struct timer

struct tachometer {
    struct gpio_irq *pirq;
    uint32_t pulse_count;
    uint8_t flags;
};

static struct tach_timer {
    struct timer time;
    uint32_t rest_ticks;
} tach_timer;

enum {FLAG_MODE0=1<<0, FLAG_MODE1=1<<1, FLAGMODE2=1<<2,
      FLAG_MODE3=1<<3, FLAG_EN=1<<4};

static struct task_wake tach_wake;

void command_config_tachometer(uint32_t *args);

static uint_fast8_t
tach_send_event(struct timer *t) {
    sched_wake_task(&tach_wake);
    tach_timer.time.waketime += tach_timer.rest_ticks;
    return SF_RESCHEDULE;
}

static void
tach_pulse_event(uint8_t oid) {
    struct tachometer *tach = oid_lookup(oid, command_config_tachometer);
    tach->pulse_count++;
}

void
command_config_tachometer(uint32_t *args)
{
    struct tachometer *tach = oid_alloc(
        args[0], command_config_tachometer, sizeof(*tach));
    tach->pulse_count = 0;
    // to be safe configure pin as input with no internal pullup
    gpio_in_setup(args[1], 0);
    tach->pirq = gpio_irq_setup(args[1], args[0], tach_pulse_event);
}
DECL_COMMAND(command_config_tachometer, "config_tachometer oid=%c pin=%u");

void
command_update_tach_timer(uint32_t *args) {
    sched_del_timer(&tach_timer.time);
    tach_timer.time.func = tach_send_event;
    tach_timer.time.waketime = args[0];
    tach_timer.rest_ticks = args[1];
    if (args[1])
        sched_add_timer(&tach_timer.time);

}
DECL_COMMAND(command_update_tach_timer,
             "update_tach_timer clock=%u rest_ticks=%u");

void
command_set_tach_irq_state(uint32_t* args) {
    struct tachometer *tach = oid_lookup(args[0], command_config_tachometer);
    uint8_t mode = args[1];
    if (mode == 4 && (tach->flags & FLAG_EN)) {
        // disable
        tach->flags = 0;
        gpio_irq_update(tach->pirq, mode);
    } else if (!(tach->flags & (1 << mode))) {
        // enable IRQ or change mode
        tach->flags = FLAG_EN | (1 << mode);
        gpio_irq_update(tach->pirq, mode);
    }

}
DECL_COMMAND(command_set_tach_irq_state,
             "set_tach_irq_state oid=%c mode=%c");
void
tach_task(void) {
    if (!sched_check_wake(&tach_wake))
        return;
    uint8_t oid;
    uint32_t total_pulses;
    struct tachometer *tach;
    foreach_oid(oid, tach, command_config_tachometer) {
        if (!(tach->flags & FLAG_EN))
            continue;
        irq_disable();
        total_pulses = tach->pulse_count;
        tach->pulse_count = 0;
        irq_enable();
        sendf("tach_response oid=%c pulse_count=%u", oid, total_pulses);
    }
}
DECL_TASK(tach_task);
