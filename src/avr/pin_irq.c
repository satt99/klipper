#include "autoconf.h" // CONFIG_MACH_atmega644p
#include "command.h" // shutdown
#include "gpio.h" // gpio_interrupt
#include "irq.h"  // irq_save
#include "internal.h" // GPIO
#include "sched.h" // sched_shutdown

static void blank(uint8_t oid) {}
#if defined(EICRB)
#define IRQ_TO_CTRLREG(irq_id) (((irq_id) < 4) ? &EICRA : &EICRB)
#else
#define IRQ_TO_CTRLREG(irq_id) &EICRA
#endif
#define IRQNAME(idx) pirq ## idx
#define PIN_INTERRUPT(idx)                  \
struct gpio_irq IRQNAME(idx) = {            \
    .irq_id = INT ## idx,                   \
    .isc0 = ISC ## idx ## 0,                \
    .isc1 = ISC ## idx  ## 1,               \
    .oid = 0,                               \
    .func = blank                           \
};                                          \
ISR(INT ## idx ## _vect) {                  \
    IRQNAME(idx).func(IRQNAME(idx).oid);    \
}

PIN_INTERRUPT(0)
PIN_INTERRUPT(1)
#if CONFIG_MACH_at90usb1286 || CONFIG_MACH_at90usb646 \
    || CONFIG_MACH_atmega1280 || CONFIG_MACH_atmega2560
PIN_INTERRUPT(2)
PIN_INTERRUPT(3)
PIN_INTERRUPT(4)
PIN_INTERRUPT(5)
PIN_INTERRUPT(6)
PIN_INTERRUPT(7)
#elif CONFIG_MACH_atmega32u4
PIN_INTERRUPT(2)
PIN_INTERRUPT(3)
PIN_INTERRUPT(6)
#elif CONFIG_MACH_atmega644p || CONFIG_MACH_atmega1284p
PIN_INTERRUPT(2)
#endif

static inline struct gpio_irq *
pin_to_irq(uint8_t pin) {
    switch(pin) {
#if CONFIG_MACH_atmega168 || CONFIG_MACH_atmega328 \
    || CONFIG_MACH_atmega328p || CONFIG_MACH_atmega644p \
    || CONFIG_MACH_atmega1284p
        case GPIO('D', 2):
            return &pirq0;
        case GPIO('D', 3):
            return &pirq1;
#elif CONFIG_MACH_at90usb1286 || CONFIG_MACH_at90usb646 \
    || CONFIG_MACH_atmega32u4 || CONFIG_MACH_atmega1280 \
    || CONFIG_MACH_atmega2560
        case GPIO('D', 0):
            return &pirq0;
        case GPIO('D', 1):
            return &pirq1;
        case GPIO('D', 2):
            return &pirq2;
        case GPIO('D', 3):
            return &pirq3;
#endif
#if CONFIG_MACH_at90usb1286 || CONFIG_MACH_at90usb646 \
    || CONFIG_MACH_atmega1280 || CONFIG_MACH_atmega2560
        case GPIO('E', 4):
            return &pirq4;
        case GPIO('E', 5):
            return &pirq5;
        case GPIO('E', 6):
            return &pirq6;
        case GPIO('E', 7):
            return &pirq7;
#elif CONFIG_MACH_atmega644p || CONFIG_MACH_atmega1284p
        case GPIO('B', 2):
            return &pirq2;
#elif CONFIG_MACH_atmega32u4
        case GPIO('E', 6):
            return &pirq6;
#endif
        default:
            return NULL;
    }
}

// Set up external pin interrupt.
// oid - object id of parent to pass back through cb function
// cb - callback to exectue on IRQ

struct gpio_irq *
gpio_irq_setup(uint8_t pin, uint8_t oid, void (*cb)(uint8_t)) {
    struct gpio_irq *pirq = pin_to_irq(pin);
    if (!pirq)
        shutdown("Not an interrupt pin");
    pirq->oid = oid;
    pirq->func = cb;
    return pirq;
}

// Update the pin interrupt state.
// Modes:
// 0 - Low level Generates IRQ
// 1 - Any edge/logical change generates IRQ
// 2 - Falling edge generates IRQ
// 3 - Rising edge generates IRQ
// 4 - Disables IRQ
void
gpio_irq_update(struct gpio_irq *pirq, uint8_t mode) {
    if (mode > 4)
        shutdown("Invalid Interrupt Pin Mode");
    volatile uint8_t *ctrl = IRQ_TO_CTRLREG(pirq->irq_id);
    irqstatus_t flag = irq_save();
    EIMSK &= ~((1 << pirq->irq_id));
    *ctrl &= ~((1 << pirq->isc0) | (1 << pirq->isc1));
    switch(mode) {
        case 1:
            *ctrl |= (1 << pirq->isc0);
            break;
        case 2:
            *ctrl |= (1 << pirq->isc1);
            break;
        case 3:
            *ctrl |= ((1 << pirq->isc0) | (1 << pirq->isc1));
            break;
        case 4:
            irq_restore(flag);
            return;
    }
    EIFR = (1 << pirq->irq_id);
    EIMSK |= (1 << pirq->irq_id);
    irq_restore(flag);
}

void
gpio_irq_reset(struct gpio_irq *pirq) {
    gpio_irq_update(pirq, 4);
    pirq->oid = 0;
    pirq->func = blank;
}
