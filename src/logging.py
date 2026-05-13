from colorama import Fore, Style
from io import TextIOWrapper
import sys


class LogLevel:
    TRACE   = 0
    DEBUG   = 10
    INFO    = 20
    WARNING = 30
    ERROR   = 40
    FATAL   = 50

class Logger:
    level = LogLevel.TRACE

    def __init__(self, name='', files=[sys.stdout], err_files=[sys.stderr]):
        self.name = name
        self.files:list[TextIOWrapper] = files
        self.err_files = err_files

    def trace(self, *args, **kwargs):
        if not Logger.level <= LogLevel.TRACE:
            return

        self._log(f'{Fore.LIGHTBLACK_EX}{Style.BRIGHT}TRACE{Style.RESET_ALL}', args, kwargs)

    def debug(self, *args, **kwargs):
        if not Logger.level <= LogLevel.DEBUG:
            return

        self._log(f'{Fore.LIGHTMAGENTA_EX}{Style.BRIGHT}DEBUG{Style.RESET_ALL}', args, kwargs)
    
    def info(self, *args, **kwargs):
        if not Logger.level <= LogLevel.INFO:
            return

        self._log(f'{Fore.CYAN}{Style.BRIGHT}INFO{Style.RESET_ALL} ', args, kwargs)

    def warning(self, *args, **kwargs):
        if not Logger.level <= LogLevel.WARNING:
            return

        self._log(f'{Fore.YELLOW}{Style.BRIGHT}WARN{Style.RESET_ALL} ', args, kwargs)

    def error(self, *args, **kwargs):
        if not Logger.level <= LogLevel.ERROR:
            return

        self._log(f'{Fore.LIGHTRED_EX}{Style.BRIGHT}ERROR{Style.RESET_ALL}', args, kwargs)

    def fatal(self, *args, **kwargs):
        if not Logger.level <= LogLevel.FATAL:
            return

        self._log(f'{Fore.RED}{Style.BRIGHT}FATAL{Style.RESET_ALL}', args, kwargs)

    def _log(self, level, args, kwargs):
        a = ' '.join(args)
        kv = []
        for key, value in kwargs.items():
            kv.append(f'{key}: {value}')

        kv = '{' + ', '.join(kv) + "}" if kwargs else ""

        name = f" {Style.BRIGHT}[{Style.RESET_ALL}{self.name}{Style.BRIGHT}]{Style.RESET_ALL}" if self.name else ""
        level = f"{Style.BRIGHT}[{Style.RESET_ALL}{level}{Style.BRIGHT}]{Style.RESET_ALL}"

        for file in self.files:
            file.write(f"{level}{name} {a}{kv}\n")
            file.flush()
