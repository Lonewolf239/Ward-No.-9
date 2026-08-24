#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from game.app import App


def main():
    mode = "app"
    while mode == "app":
        result = App().run()
        if result == "editor":
            from tools.room_editor.editor import Editor
            Editor(mode="user").run()
            mode = "app"
        else:
            mode = "quit"


if __name__ == "__main__":
    main()
