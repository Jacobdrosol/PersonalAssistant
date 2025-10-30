try:
    from .app import main
except ImportError:
    from assistant_app.app import main

if __name__ == '__main__':
    main()␊
