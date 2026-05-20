# Ledock-win-wsl
Windows GUI for LeDock molecular docking via WSL2. Features auto WSL detection, protein prep, ligand management, and pocket definition. Built with PyQt5.基于 WSL2 的 LeDock 分子对接 Windows GUI。支持自动环境检测、蛋白处理、配体管理及口袋定义，基于 PyQt5 开发，科研开箱即用。
Win 系统下运行Ledock不稳定，闪退。Wsl下运行稳定，但是操作麻烦。
代码直接在win下界面操作，后台在wsl中运行，运行结束后生成的文件会被返回到win系统中受体文件夹中。
需要以下环境：
1.Python 环境 + PyQt5 依赖
2.WSL2 + 任意 Linux 发行版
3.WSL2 中安装 LeDock：lepro_linux_x86（蛋白处理）和 ledock_linux_x86（对接程序）需在 WSL 的 PATH 中或工作目录内
