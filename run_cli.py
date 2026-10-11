# -*- coding: utf-8 -*-
"""命令行启动器(可直接双击/在终端运行)。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from province_splitter.cli import main  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) == 1:
        # 无参数时给出一个默认的示例(分析模式)
        print("未提供参数。示例:")
        print('  python run_cli.py --mod "C:\\Users\\Administrator\\Documents\\Paradox Interactive'
              '\\Hearts of Iron IV\\mod\\Project_IRIS" --out "D:\\地块细化\\output" '
              '--threshold 200 --scope all --analyze')
        sys.exit(1)
    sys.exit(main())
