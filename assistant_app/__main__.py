import sys


def _resolve_main():
    use_qt = "--qt" in sys.argv or "--ui=qt" in sys.argv
    if use_qt:
        sys.argv = [arg for arg in sys.argv if arg not in {"--qt", "--ui=qt"}]
        try:
            from .qt_app import main
        except ImportError:
            from assistant_app.qt_app import main
        return main
    try:
        from .app import main
    except ImportError:
        from assistant_app.app import main
    return main

if __name__ == '__main__':
    _resolve_main()()
