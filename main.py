"""Entry point di Data Explorer: python main.py [--port N] [--db file.duckdb] [file ...]"""
from data_explorer.app import main

if __name__ in {"__main__", "__mp_main__"}:
    main()
