import os
import sys
import subprocess
import shutil
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QTextEdit, QGroupBox,
    QMessageBox, QProgressBar, QDoubleSpinBox
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont

WSL_DISTRO = None
WSL_WORK_DIR = None


def setup_wsl_env():
    """Auto-detect WSL distro and home directory"""
    global WSL_DISTRO, WSL_WORK_DIR
    try:
        res = subprocess.run(['wsl', '-l', '--quiet'], capture_output=True, text=True, timeout=10, encoding='utf-8', errors='ignore')
        output = res.stdout.replace('\x00', '').strip()
        distros = [d.strip().strip('*').strip() for d in output.splitlines() if d.strip()]
        
        WSL_DISTRO = distros[0] if distros else None
            
        cmd = ['wsl']
        if WSL_DISTRO:
            cmd.extend(['-d', WSL_DISTRO])
        cmd.extend(['-e', 'bash', '-c', 'echo $HOME'])
        
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, encoding='utf-8', errors='ignore')
        home = res.stdout.replace('\x00', '').strip().replace('\r', '')
        if not home:
            home = '/root'
            
        WSL_WORK_DIR = f"{home}/ledock_work"
        return True, ""
    except Exception as e:
        return False, f"WSL 环境检测失败: {str(e)}"


def run_wsl_command(cmd, timeout=300):
    """Execute command in WSL2 and return (success, output)"""
    wsl_args = ['wsl']
    if WSL_DISTRO:
        wsl_args.extend(['-d', WSL_DISTRO])
    wsl_args.extend(['-e', 'bash', '-c', cmd])
    
    try:
        result = subprocess.run(
            wsl_args, capture_output=True, text=True,
            timeout=timeout, encoding='utf-8', errors='replace'
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            error = result.stderr.strip()
            return False, error if error else output
        return True, output
    except subprocess.TimeoutExpired:
        return False, "命令执行超时"
    except Exception as e:
        return False, str(e)


def win_to_wsl_path(win_path):
    """Convert Windows path to WSL path"""
    path = win_path.replace('/', '\\')
    if len(path) >= 2 and path[1] == ':':
        drive = path[0].lower()
        rest = path[2:].lstrip('\\').replace('\\', '/')
        return f"/mnt/{drive}/{rest}"
    return win_path.replace('\\', '/')


def wsl_to_win_path(wsl_path):
    """Convert WSL path to Windows UNC path"""
    distro = WSL_DISTRO if WSL_DISTRO else ""
    return f"\\\\wsl$\\{distro}{wsl_path.replace('/', '\\')}"


class ProteinProcessThread(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, protein_path):
        super().__init__()
        self.protein_path = protein_path

    def run(self):
        try:
            self.progress.emit("正在创建 WSL2 工作目录...")
            run_wsl_command(f'mkdir -p {WSL_WORK_DIR}')
            run_wsl_command(f'rm -rf {WSL_WORK_DIR}/*')

            protein_name = os.path.basename(self.protein_path)
            wsl_protein_path = f"{WSL_WORK_DIR}/{protein_name}"

            self.progress.emit("正在复制蛋白文件到 WSL2...")
            wsl_src = win_to_wsl_path(self.protein_path)
            success, _ = run_wsl_command(f'cp "{wsl_src}" "{wsl_protein_path}"')
            if not success:
                self.finished.emit(False, "复制蛋白文件失败")
                return

            self.progress.emit("正在处理蛋白结构...")
            success, output = run_wsl_command(
                f'cd {WSL_WORK_DIR} && lepro_linux_x86 "{protein_name}"',
                timeout=600
            )
            if not success:
                self.finished.emit(False, f"蛋白处理失败:\n{output}")
                return

            success, _ = run_wsl_command(f'test -f {WSL_WORK_DIR}/pro.pdb')
            if not success:
                self.finished.emit(False, "未生成 pro.pdb 文件")
                return

            success, _ = run_wsl_command(f'test -f {WSL_WORK_DIR}/dock.in')
            if not success:
                self.finished.emit(False, "未生成 dock.in 文件")
                return

            self.finished.emit(True, "蛋白处理完成！已生成 pro.pdb 和 dock.in")
        except Exception as e:
            self.finished.emit(False, f"处理过程出错:\n{str(e)}")


class DockingThread(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, ligand_paths, coords, num_poses):
        super().__init__()
        self.ligand_paths = ligand_paths
        self.coords = coords
        self.num_poses = num_poses

    def run(self):
        try:
            for i, ligand_path in enumerate(self.ligand_paths):
                ligand_name = os.path.basename(ligand_path)
                wsl_ligand_path = f"{WSL_WORK_DIR}/{ligand_name}"

                self.progress.emit(f"正在复制配体文件 {i+1}/{len(self.ligand_paths)}: {ligand_name}...")
                wsl_src = win_to_wsl_path(ligand_path)
                success, _ = run_wsl_command(f'cp "{wsl_src}" "{wsl_ligand_path}"')
                if not success:
                    self.finished.emit(False, f"复制配体文件 {ligand_name} 失败")
                    return

            self.progress.emit("正在生成 ligands 配置文件...")
            ligand_names = [os.path.basename(p) for p in self.ligand_paths]
            ligands_content = "\n".join(ligand_names)
            wsl_ligands_unc = wsl_to_win_path(f"{WSL_WORK_DIR}/ligands")
            try:
                with open(wsl_ligands_unc, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(ligands_content + "\n")
            except Exception as e:
                self.finished.emit(False, f"生成 ligands 失败:\n{str(e)}")
                return

            self.progress.emit("正在修改 dock.in 参数...")
            wsl_dock_unc = wsl_to_win_path(f"{WSL_WORK_DIR}/dock.in")
            success, _ = run_wsl_command(f'test -f {WSL_WORK_DIR}/dock.in')
            if not success:
                self.finished.emit(False, "未找到 dock.in 文件，请先处理蛋白")
                return

            xmin, xmax, ymin, ymax, zmin, zmax = self.coords
            dock_content = (
                f"Receptor\n"
                f"pro.pdb\n"
                f"\n"
                f"RMSD\n"
                f"1.0\n"
                f"\n"
                f"Binding pocket\n"
                f"{xmin} {xmax}\n"
                f"{ymin} {ymax}\n"
                f"{zmin} {zmax}\n"
                f"\n"
                f"Number of binding poses\n"
                f"{self.num_poses}\n"
                f"\n"
                f"Ligands list\n"
                f"ligands\n"
                f"\n"
                f"END\n"
            )
            try:
                with open(wsl_dock_unc, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(dock_content)
            except Exception as e:
                self.finished.emit(False, f"写入 dock.in 失败:\n{str(e)}")
                return

            self.progress.emit("正在执行分子对接...")
            success, output = run_wsl_command(
                f'cd {WSL_WORK_DIR} && ledock_linux_x86 dock.in',
                timeout=3600
            )
            if not success:
                self.finished.emit(False, f"对接失败:\n{output}")
                return

            success, dok_files = run_wsl_command(f'ls {WSL_WORK_DIR}/*.dok 2>/dev/null')
            if not success or not dok_files.strip():
                self.finished.emit(False, "未生成对接结果文件 (.dok)")
                return

            dok_count = len(dok_files.strip().split('\n'))
            self.progress.emit(f"对接完成，生成 {dok_count} 个结果文件")
            self.finished.emit(True, "对接完成！")
        except Exception as e:
            self.finished.emit(False, f"对接过程出错:\n{str(e)}")


class ResultCopyThread(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, dest_folder):
        super().__init__()
        self.dest_folder = dest_folder

    def run(self):
        try:
            self.progress.emit("正在将结果复制回 Windows...")
            os.makedirs(self.dest_folder, exist_ok=True)

            wsl_unc = wsl_to_win_path(WSL_WORK_DIR)
            if not os.path.exists(wsl_unc):
                self.finished.emit(False, "无法访问 WSL2 工作目录")
                return

            for filename in os.listdir(wsl_unc):
                src = os.path.join(wsl_unc, filename)
                dst = os.path.join(self.dest_folder, filename)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)

            self.finished.emit(True, f"所有文件已复制到:\n{self.dest_folder}")
        except Exception as e:
            self.finished.emit(False, f"复制文件失败:\n{str(e)}")


class LeDockUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.protein_path = None
        self.ligand_paths = []
        self.protein_folder = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("LeDock 分子对接系统")
        self.resize(700, 650)
        self.setStyleSheet(self.get_style())

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(15)

        # 蛋白处理组
        protein_group = QGroupBox("1. 蛋白处理")
        protein_layout = QVBoxLayout()

        row1 = QHBoxLayout()
        self.protein_label = QLabel("未选择蛋白文件")
        self.protein_label.setStyleSheet("color: #888;")
        row1.addWidget(self.protein_label, 1)

        btn_select_protein = QPushButton("选择蛋白 (PDB)")
        btn_select_protein.clicked.connect(self.select_protein)
        row1.addWidget(btn_select_protein)

        btn_process = QPushButton("处理蛋白")
        btn_process.clicked.connect(self.process_protein)
        row1.addWidget(btn_process)
        protein_layout.addLayout(row1)
        protein_group.setLayout(protein_layout)
        main_layout.addWidget(protein_group)

        # 配体与参数组
        ligand_group = QGroupBox("2. 配体与对接参数")
        ligand_layout = QVBoxLayout()

        row2 = QHBoxLayout()
        self.ligand_label = QLabel("未选择配体文件")
        self.ligand_label.setStyleSheet("color: #888;")
        row2.addWidget(self.ligand_label, 1)

        btn_select_ligand = QPushButton("选择配体 (MOL2, 可多选)")
        btn_select_ligand.clicked.connect(self.select_ligand)
        row2.addWidget(btn_select_ligand)
        ligand_layout.addLayout(row2)

        coord_layout = QVBoxLayout()
        coord_layout.addWidget(QLabel("Binding Pocket 坐标:"))

        coord_grid = QVBoxLayout()
        labels = ["X 最小", "X 最大", "Y 最小", "Y 最大", "Z 最小", "Z 最大"]
        self.coord_inputs = []
        for i in range(0, 6, 2):
            row = QHBoxLayout()
            for j in range(2):
                label = QLabel(f"{labels[i+j]}:")
                label.setFixedWidth(60)
                row.addWidget(label)
                spin = QDoubleSpinBox()
                spin.setRange(-999.0, 999.0)
                spin.setSingleStep(5.0)
                spin.setDecimals(1)
                spin.setValue(0.0)
                spin.setFixedWidth(90)
                spin.setAlignment(Qt.AlignCenter)
                self.coord_inputs.append(spin)
                row.addWidget(spin)
                if j == 0:
                    row.addSpacing(20)
            coord_grid.addLayout(row)
        coord_layout.addLayout(coord_grid)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("对接数量 (Number of binding poses):"))
        self.num_poses_input = QLineEdit("1")
        self.num_poses_input.setFixedWidth(60)
        row3.addWidget(self.num_poses_input)
        row3.addStretch()
        coord_layout.addLayout(row3)

        ligand_layout.addLayout(coord_layout)
        ligand_group.setLayout(ligand_layout)
        main_layout.addWidget(ligand_group)

        # 对接按钮
        btn_dock = QPushButton("开始对接")
        btn_dock.setObjectName("primaryBtn")
        btn_dock.clicked.connect(self.start_docking)
        main_layout.addWidget(btn_dock)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # 日志区域
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(200)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

    def get_style(self):
        return """
            QMainWindow { background-color: #f5f5f5; }
            QGroupBox {
                font-weight: bold;
                border: 2px solid #cccccc;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QPushButton {
                padding: 8px 16px;
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #e8e8e8; }
            QPushButton#primaryBtn {
                background-color: #2196F3;
                color: white;
                border: none;
                padding: 12px 24px;
                font-size: 14px;
            }
            QPushButton#primaryBtn:hover { background-color: #1976D2; }
            QPushButton:disabled { background-color: #dddddd; color: #888; }
            QLineEdit {
                padding: 6px;
                border: 1px solid #cccccc;
                border-radius: 4px;
            }
            QTextEdit {
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: #fafafa;
                font-family: Consolas, monospace;
            }
            QProgressBar {
                border: 1px solid #cccccc;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk { background-color: #2196F3; }
        """

    def log(self, msg):
        self.log_text.append(msg)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def select_protein(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择蛋白 PDB 文件", "", "PDB Files (*.pdb);;All Files (*)"
        )
        if path:
            self.protein_path = path
            self.protein_folder = os.path.dirname(path)
            self.protein_label.setText(os.path.basename(path))
            self.protein_label.setStyleSheet("color: #333;")
            self.log(f"已选择蛋白: {path}")

    def select_ligand(self):
        default_dir = self.protein_folder if self.protein_folder else ""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择配体 MOL2 文件（可多选）", default_dir,
            "MOL2 Files (*.mol2);;All Files (*)"
        )
        if paths:
            self.ligand_paths = paths
            names = [os.path.basename(p) for p in paths]
            if len(names) == 1:
                self.ligand_label.setText(names[0])
            else:
                self.ligand_label.setText(f"{len(names)} 个配体: {', '.join(names[:3])}{'...' if len(names) > 3 else ''}")
            self.ligand_label.setStyleSheet("color: #333;")
            self.log(f"已选择 {len(paths)} 个配体: {', '.join(names)}")

    def process_protein(self):
        if not self.protein_path:
            QMessageBox.warning(self, "警告", "请先选择蛋白 PDB 文件！")
            return

        self.log("开始处理蛋白...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.thread = ProteinProcessThread(self.protein_path)
        self.thread.progress.connect(self.log)
        self.thread.finished.connect(self.on_protein_finished)
        self.thread.start()

    def on_protein_finished(self, success, msg):
        self.progress_bar.setVisible(False)
        self.log(msg)
        if success:
            QMessageBox.information(self, "成功", msg)
        else:
            QMessageBox.critical(self, "错误", msg)

    def get_coords(self):
        coords = []
        for spin in self.coord_inputs:
            coords.append(spin.value())
        return coords

    def start_docking(self):
        if not self.protein_path:
            QMessageBox.warning(self, "警告", "请先选择并处理蛋白！")
            return
        if not self.ligand_paths:
            QMessageBox.warning(self, "警告", "请先选择配体 MOL2 文件！")
            return

        coords = self.get_coords()

        try:
            num_poses = int(self.num_poses_input.text())
            if num_poses < 1:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "警告", "对接数量必须为正整数！")
            return

        dest = self.protein_folder if self.protein_folder else os.path.dirname(self.ligand_paths[0])

        self.log(f"开始分子对接，共 {len(self.ligand_paths)} 个配体...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.dock_thread = DockingThread(self.ligand_paths, coords, num_poses)
        self.dock_thread.progress.connect(self.log)
        self.dock_thread.finished.connect(
            lambda s, m: self.on_docking_finished(s, m, dest)
        )
        self.dock_thread.start()

    def on_docking_finished(self, success, msg, dest_folder):
        self.log(msg)
        if not success:
            self.progress_bar.setVisible(False)
            QMessageBox.critical(self, "错误", msg)
            return

        self.progress_bar.setRange(0, 0)
        self.log("开始复制结果文件...")

        self.copy_thread = ResultCopyThread(dest_folder)
        self.copy_thread.progress.connect(self.log)
        self.copy_thread.finished.connect(self.on_copy_finished)
        self.copy_thread.start()

    def on_copy_finished(self, success, msg):
        self.progress_bar.setVisible(False)
        self.log(msg)
        if success:
            QMessageBox.information(self, "成功", msg)
        else:
            QMessageBox.critical(self, "错误", msg)


def main():
    success, err = setup_wsl_env()
    if not success:
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "WSL 配置错误", err)
        sys.exit(1)
        
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    window = LeDockUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
