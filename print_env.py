# -*- coding: utf-8 -*-
"""打印当前进程的全部环境变量"""
import os

for key, value in sorted(os.environ.items()):
    print(f"{key}={value}")
