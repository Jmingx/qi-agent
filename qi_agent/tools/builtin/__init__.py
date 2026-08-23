"""内置工具层：1 工具 1 文件，import 即注册（各文件导入时执行 register）。

分层（方案 2026-08-23-工具与插件系统分层方案）：
- 机制层（tools/ 根）：工具系统怎么运作——registry/executor/decision
- 工具层（本目录）：系统有哪些能力——新增工具 = 本目录新建文件 + 在此导入

注册触发链路：tools/__init__ → from . import builtin → 本文件 import 各工具
→ 各工具模块 register() 自注册。漏一个 import = 该工具静默不注册。
"""

from qi_agent.tools.builtin import (  # noqa: F401  导入即注册
    clarify,
    delegate_task,
    file_delete,
    get_time,
    list_dir,
    patch,
    read_file,
    run_python,
    search_files,
    shell,
    todo,
    web_extract,
    web_search,
    write_file,
)
