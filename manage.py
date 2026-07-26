#!/usr/bin/env python
"""Django management entry point for the CIM Analyst web front end."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cimweb.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
