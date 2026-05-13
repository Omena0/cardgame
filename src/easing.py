import enum
import math

class EasingType(enum.IntEnum):
    Linear  = enum.auto()
    Sine    = enum.auto()
    Quad    = enum.auto()
    Cubic   = enum.auto()
    Quart   = enum.auto()
    Quint   = enum.auto()
    Expo    = enum.auto()
    Circ    = enum.auto()
    Back    = enum.auto()
    Elastic = enum.auto()
    Bounce  = enum.auto()


# Easing mode constants
EASE_IN = 0
EASE_OUT = 1
EASE_INOUT = 2

# Individual easing functions
def linear(t: float) -> float:
    """Linear easing"""
    return t

def sine_in(t: float) -> float:
    """Sine easing in"""
    return 1 - math.cos((t * math.pi) / 2)

def sine_out(t: float) -> float:
    """Sine easing out"""
    return math.sin((t * math.pi) / 2)

def sine_inout(t: float) -> float:
    """Sine easing in/out"""
    return -(math.cos(math.pi * t) - 1) / 2

def quad_in(t: float) -> float:
    """Quadratic easing in"""
    return t * t

def quad_out(t: float) -> float:
    """Quadratic easing out"""
    return 1 - (1 - t) * (1 - t)

def quad_inout(t: float) -> float:
    """Quadratic easing in/out"""
    return 2 * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 2) / 2

def cubic_in(t: float) -> float:
    """Cubic easing in"""
    return t * t * t

def cubic_out(t: float) -> float:
    """Cubic easing out"""
    return 1 - pow(1 - t, 3)

def cubic_inout(t: float) -> float:
    """Cubic easing in/out"""
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2

def quart_in(t: float) -> float:
    """Quartic easing in"""
    return t * t * t * t

def quart_out(t: float) -> float:
    """Quartic easing out"""
    return 1 - pow(1 - t, 4)

def quart_inout(t: float) -> float:
    """Quartic easing in/out"""
    return 8 * t * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 4) / 2

def quint_in(t: float) -> float:
    """Quintic easing in"""
    return t * t * t * t * t

def quint_out(t: float) -> float:
    """Quintic easing out"""
    return 1 - pow(1 - t, 5)

def quint_inout(t: float) -> float:
    """Quintic easing in/out"""
    return 16 * t * t * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 5) / 2

def expo_in(t: float) -> float:
    """Exponential easing in"""
    return 0 if t == 0 else pow(2, 10 * t - 10)

def expo_out(t: float) -> float:
    """Exponential easing out"""
    return 1 if t == 1 else 1 - pow(2, -10 * t)

def expo_inout(t: float) -> float:
    """Exponential easing in/out"""
    if t == 0:
        return 0
    elif t == 1:
        return 1
    elif t < 0.5:
        return pow(2, 20 * t - 10) / 2
    else:
        return (2 - pow(2, -20 * t + 10)) / 2

def circ_in(t: float) -> float:
    """Circular easing in"""
    return 1 - math.sqrt(1 - pow(t, 2))

def circ_out(t: float) -> float:
    """Circular easing out"""
    return math.sqrt(1 - pow(t - 1, 2))

def circ_inout(t: float) -> float:
    """Circular easing in/out"""
    if t < 0.5:
        return (1 - math.sqrt(1 - pow(2 * t, 2))) / 2
    else:
        return (math.sqrt(1 - pow(-2 * t + 2, 2)) + 1) / 2

def back_in(t: float) -> float:
    """Back easing in"""
    c1 = 1.70158
    c3 = c1 + 1
    return c3 * t * t * t - c1 * t * t

def back_out(t: float) -> float:
    """Back easing out"""
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)

def back_inout(t: float) -> float:
    """Back easing in/out"""
    c1 = 1.70158
    c2 = c1 * 1.525
    if t < 0.5:
        return (pow(2 * t, 2) * ((c2 + 1) * 2 * t - c2)) / 2
    else:
        return (pow(2 * t - 2, 2) * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2

def elastic_in(t: float) -> float:
    """Elastic easing in"""
    if t == 0:
        return 0
    elif t == 1:
        return 1
    else:
        c4 = (2 * math.pi) / 3
        return -pow(2, 10 * t - 10) * math.sin((t * 10 - 10.75) * c4)

def elastic_out(t: float) -> float:
    """Elastic easing out"""
    if t == 0:
        return 0
    elif t == 1:
        return 1
    else:
        c4 = (2 * math.pi) / 3
        return pow(2, -10 * t) * math.sin((t * 10 - 0.75) * c4) + 1

def elastic_inout(t: float) -> float:
    """Elastic easing in/out"""
    if t == 0:
        return 0
    elif t == 1:
        return 1
    else:
        c5 = (2 * math.pi) / 4.5
        if t < 0.5:
            return -(pow(2, 20 * t - 10) * math.sin((20 * t - 11.125) * c5)) / 2
        else:
            return (pow(2, -20 * t + 10) * math.sin((20 * t - 11.125) * c5)) / 2 + 1

def bounce_out(t: float) -> float:
    """Bounce easing out"""
    n1 = 7.5625
    d1 = 2.75
    
    if t < 1 / d1:
        return n1 * t * t
    elif t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    elif t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    else:
        t -= 2.625 / d1
        return n1 * t * t + 0.984375

def bounce_in(t: float) -> float:
    """Bounce easing in"""
    return 1 - bounce_out(1 - t)

def bounce_inout(t: float) -> float:
    """Bounce easing in/out"""
    if t < 0.5:
        return (1 - bounce_out(1 - 2 * t)) / 2
    else:
        return (1 + bounce_out(2 * t - 1)) / 2

# Mapping of easing types to functions
EASING_FUNCTIONS = {
    EasingType.Linear: {
        EASE_IN: linear,
        EASE_OUT: linear,
        EASE_INOUT: linear,
    },
    EasingType.Sine: {
        EASE_IN: sine_in,
        EASE_OUT: sine_out,
        EASE_INOUT: sine_inout,
    },
    EasingType.Quad: {
        EASE_IN: quad_in,
        EASE_OUT: quad_out,
        EASE_INOUT: quad_inout,
    },
    EasingType.Cubic: {
        EASE_IN: cubic_in,
        EASE_OUT: cubic_out,
        EASE_INOUT: cubic_inout,
    },
    EasingType.Quart: {
        EASE_IN: quart_in,
        EASE_OUT: quart_out,
        EASE_INOUT: quart_inout,
    },
    EasingType.Quint: {
        EASE_IN: quint_in,
        EASE_OUT: quint_out,
        EASE_INOUT: quint_inout,
    },
    EasingType.Expo: {
        EASE_IN: expo_in,
        EASE_OUT: expo_out,
        EASE_INOUT: expo_inout,
    },
    EasingType.Circ: {
        EASE_IN: circ_in,
        EASE_OUT: circ_out,
        EASE_INOUT: circ_inout,
    },
    EasingType.Back: {
        EASE_IN: back_in,
        EASE_OUT: back_out,
        EASE_INOUT: back_inout,
    },
    EasingType.Elastic: {
        EASE_IN: elastic_in,
        EASE_OUT: elastic_out,
        EASE_INOUT: elastic_inout,
    },
    EasingType.Bounce: {
        EASE_IN: bounce_in,
        EASE_OUT: bounce_out,
        EASE_INOUT: bounce_inout,
    },
}

def ease(easing_type: EasingType, mode: int, t: float) -> float:
    """
    Apply an easing function to a normalized time value.
    
    Args:
        easing_type: The type of easing (from EasingType enum)
        mode: The easing mode - 0 for in, 1 for out, 2 for inout
        t: Normalized time value (0 to 1)
    
    Returns:
        The eased value between 0 and 1
    """
    if easing_type not in EASING_FUNCTIONS:
        raise ValueError(f"Unknown easing type: {easing_type}")
    if mode not in EASING_FUNCTIONS[easing_type]:
        raise ValueError(f"Unknown easing mode: {mode}")
    
    # Clamp t to [0, 1]
    t = max(0, min(1, t))
    
    return EASING_FUNCTIONS[easing_type][mode](t)


