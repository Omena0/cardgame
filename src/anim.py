"""
Animation library with a blocking and non-blocking impl.

Blocking:
  A generator that gives new positions

Non-blocking:
  A simple callback or function decorator
"""

import time
import threading
from functools import wraps
from src.easing import ease, EasingType


def distance_based_duration(easing_time: float = 0.05, middle_time_per_unit: float = 0.005):
    """
    Returns a duration_getter function that calculates duration based on distance.
    
    The duration consists of:
    - Fixed easing time (acceleration/deceleration curves at start and end)
    - Variable middle time that scales with the distance
    
    Args:
        easing_time: Time in seconds for the easing curves (start and end)
        middle_time_per_unit: Time in seconds per unit of distance for the middle rotation
    
    Returns:
        A function that takes (current, target) tuples and returns duration
    """
    def duration_getter(current, target):
        distance = abs(target[0] - current[0])
        return easing_time + (distance * middle_time_per_unit)
    return duration_getter


def anim_generator(start: tuple, end: tuple, type: EasingType, inout: int, duration: float = 1.0, fps: int = 60):
    frame_time = 1.0 / fps
    elapsed = 0.0

    while elapsed <= duration:
        # Calculate normalized time (0 to 1)
        t = elapsed / duration if duration > 0 else 1.0

        # Apply easing
        eased_t = ease(type, inout, t)

        # Interpolate values
        values = []
        for i in range(len(start)):
            start_val = start[i]
            end_val = end[i]
            interpolated = start_val + (end_val - start_val) * eased_t
            values.append(interpolated)

        yield tuple(values)

        elapsed += frame_time

def anim(start: tuple, end: tuple, type: EasingType, inout: int, duration: float = 1.0, fps: int = 60):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            frame_time = 1.0 / fps
            elapsed = 0.0

            while elapsed <= duration:
                # Calculate normalized time (0 to 1)
                t = elapsed / duration if duration > 0 else 1.0

                # Apply easing
                eased_t = ease(type, inout, t)

                # Interpolate values
                values = []
                for i in range(len(start)):
                    start_val = start[i]
                    end_val = end[i]
                    interpolated = start_val + (end_val - start_val) * eased_t
                    values.append(interpolated)

                # Call the decorated function with interpolated values
                func(*values, *args, **kwargs)

                elapsed += frame_time

                # Sleep to maintain fps
                time.sleep(frame_time)

        def to_thread(*args, **kwargs):
            """Start the animation in a new thread"""
            thread = threading.Thread(target=wrapper, args=args, kwargs=kwargs, daemon=False)
            thread.start()
            return thread

        wrapper.to_thread = to_thread
        return wrapper
    return decorator


def anim_towards_generator(start: tuple, target_getter, type: EasingType, inout: int, duration: float = 1.0, fps: int = 60):
    """
    Generator that continuously animates towards a changing target.
    target_getter is a callable that returns the current target tuple.
    """
    frame_time = 1.0 / fps
    current = list(start)
    
    while True:
        target = target_getter()
        
        # Check if we're close enough to target
        all_close = all(abs(current[i] - target[i]) < 0.01 for i in range(len(current)))
        if all_close:
            yield tuple(current)
            time.sleep(frame_time)
            continue
        
        # Animate from current to target
        elapsed = 0.0
        start_pos = tuple(current)
        
        while elapsed <= duration:
            target = target_getter()
            
            # If target changed significantly, break and restart
            if any(abs(target[i] - current[i]) > 0.5 for i in range(len(target))):
                break
            
            # Calculate normalized time
            t = elapsed / duration if duration > 0 else 1.0
            
            # Apply easing
            eased_t = ease(type, inout, t)
            
            # Interpolate values
            values = []
            for i in range(len(start_pos)):
                start_val = start_pos[i]
                end_val = target[i]
                interpolated = start_val + (end_val - start_val) * eased_t
                values.append(interpolated)
                current[i] = interpolated
            
            yield tuple(values)
            
            elapsed += frame_time
            time.sleep(frame_time)

def anim_towards(start: tuple, target_getter, type: EasingType, inout: int, duration: float = 1.0, fps: int = 60, duration_getter=None):
    """
    Decorator for continuous animation towards a changing target with velocity-based movement.
    
    Uses constant velocity movement with ease-in only at the start.
    When target changes, smoothly adjusts direction without restarting easing.
    
    Args:
        start: Starting values tuple
        target_getter: Callable that returns the current target tuple
        type: EasingType (for initial acceleration phase only)
        inout: Easing mode
        duration: Time per unit distance (e.g., 0.3 = 0.3 seconds per unit)
        fps: Frames per second
        duration_getter: Optional callable(current, target) that returns duration
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            frame_time = 1.0 / fps
            current = list(start)
            velocity = [0.0] * len(start)  # Current velocity per dimension
            target_velocity = [0.0] * len(start)  # Target velocity per dimension
            
            ease_in_duration = 0.1  # How long to ease in at the start
            ease_in_elapsed = 0.0  # Time spent in ease-in phase
            has_started = False  # Track if animation has started
            
            last_frame_time = time.time()
            
            while True:
                target = list(target_getter())
                
                # Check if we're close enough to target
                all_close = all(abs(current[i] - target[i]) < 0.01 for i in range(len(current)))
                if all_close:
                    velocity = [0.0] * len(start)
                    target_velocity = [0.0] * len(start)
                    time.sleep(frame_time * 0.5)
                    continue
                
                # Calculate distance
                distance = sum(abs(target[i] - current[i]) for i in range(len(target)))
                
                if duration_getter:
                    total_duration = duration_getter(current, target)
                else:
                    # Use fixed duration for easing, then constant velocity for the motion
                    # This makes speed increase proportionally with distance
                    total_duration = duration
                
                # Calculate constant velocity magnitude (units per second)
                # Speed directly scales with distance - 2x distance = 2x speed
                velocity_magnitude = distance / max(0.01, total_duration)
                
                # Calculate target velocity for each dimension
                for i in range(len(target)):
                    if distance > 0:
                        direction = 1.0 if target[i] > current[i] else -1.0
                        distance_i = abs(target[i] - current[i])
                        target_velocity[i] = direction * velocity_magnitude * (distance_i / distance)
                    else:
                        target_velocity[i] = 0.0
                
                # Ease-in phase: smoothly accelerate to target velocity
                if not has_started:
                    has_started = True
                    ease_in_elapsed = 0.0
                
                if ease_in_elapsed < ease_in_duration:
                    # Apply easing to acceleration
                    t = ease_in_elapsed / ease_in_duration
                    eased_t = ease(type, inout, t)
                    
                    # Accelerate towards target velocity
                    for i in range(len(velocity)):
                        velocity[i] = target_velocity[i] * eased_t
                    
                    ease_in_elapsed += frame_time
                else:
                    # Constant velocity phase: move at target velocity
                    velocity = list(target_velocity)
                
                # Move current by velocity
                for i in range(len(current)):
                    current[i] += velocity[i] * frame_time
                    
                    # Stop if we've gone past the target
                    if velocity[i] > 0 and current[i] > target[i]:
                        current[i] = target[i]
                    elif velocity[i] < 0 and current[i] < target[i]:
                        current[i] = target[i]
                
                func(*current, *args, **kwargs)
                
                # Frame rate limiting
                now = time.time()
                time_to_next_frame = frame_time - (now - last_frame_time)
                if time_to_next_frame > 0:
                    time.sleep(time_to_next_frame)
                last_frame_time = time.time()

        def to_thread(*args, **kwargs):
            """Start the animation in a new thread"""
            thread = threading.Thread(target=wrapper, args=args, kwargs=kwargs, daemon=True)
            thread.start()
            return thread

        wrapper.to_thread = to_thread
        return wrapper
    return decorator

