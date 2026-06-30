import cProfile
import pstats
from pstats import SortKey
from functools import wraps


def profile(top=15, sort=SortKey.TIME):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            profiler = cProfile.Profile()
            profiler.enable()
            result = func(*args, **kwargs)
            profiler.disable()

            stats = pstats.Stats(profiler).sort_stats(sort)
            stats.print_stats(top)
            return result

        return wrapper

    return decorator
