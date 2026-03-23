import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QTextEdit, QPushButton, QLabel, QFrame, QHBoxLayout,
                             QLineEdit)
from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal, QObject, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QScreen, QPainter, QColor, QBrush, QPen, QLinearGradient, QPainterPath

MIN_BTN_STYLE = """
    QPushButton {
        background-color: rgba(255, 255, 255, 50);
        border: none;
        border-radius: 15px;
        color: white;
        font-size: 16px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 100);
    }
    QPushButton:pressed {
        background-color: rgba(255, 255, 255, 150);
    }
"""

CLEAR_BTN_STYLE = """
    QPushButton {
        background-color: rgba(255, 255, 255, 50);
        border: none;
        border-radius: 15px;
        color: white;
        font-size: 14px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 100);
    }
    QPushButton:pressed {
        background-color: rgba(255, 255, 255, 150);
    }
"""

SEND_BTN_STYLE = """
    QPushButton {
        background-color: rgba(255, 255, 255, 50);
        border: none;
        border-radius: 15px;
        color: white;
        font-size: 14px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 100);
    }
    QPushButton:pressed {
        background-color: rgba(255, 255, 255, 150);
    }
"""

CLOSE_BTN_STYLE = """
    QPushButton {
        background-color: rgba(255, 255, 255, 50);
        border: none;
        border-radius: 15px;
        color: white;
        font-size: 16px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #E74C3C;
    }
    QPushButton:pressed {
        background-color: #C0392B;
    }
"""


class FloatingBall(QFrame):
    """悬浮球类"""

    def __init__(self):
        super().__init__()
        self.init_ui()
        self.screen_edge = None

    def init_ui(self):
        # 设置悬浮球的基本属性 - 160*30 长方形
        self.setFixedSize(160, 30)
        self.setWindowTitle('悬浮球')

        # 移除窗口框架，设置为工具窗口
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.Tool |
                            Qt.WindowType.WindowStaysOnTopHint)

        # 设置窗口透明背景
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 始终保持在最前（包括全屏应用）
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        # 设置在所有桌面层上（不被其他应用覆盖）
        if hasattr(Qt.WindowType, 'WindowDoesNotAcceptFocus'):
            self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)

        # 记录鼠标位置
        self.click_position = QPoint()

        # 创建主布局
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(5, 2, 5, 2)
        main_layout.setSpacing(8)
        
        # 创建四个圆形按钮
        self.start_btn = QPushButton("开始")
        self.stop_btn = QPushButton("停止")
        self.game_helper_btn = QPushButton("AI助手")  # 修改按钮文本为"AI助手"
        self.pin_btn = QPushButton("固定")

        # 统一按钮样式
        button_style = """
            QPushButton {
                background-color: rgba(255, 255, 255, 50);
                border: none;
                border-radius: 12px;
                color: white;
                font-size: 10px;
                font-weight: bold;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 100);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 150);
            }
        """
        
        self.start_btn.setStyleSheet(button_style)
        self.stop_btn.setStyleSheet(button_style)
        self.game_helper_btn.setStyleSheet(button_style)
        self.pin_btn.setStyleSheet(button_style)

        # 将按钮添加到布局
        main_layout.addWidget(self.start_btn)
        main_layout.addWidget(self.stop_btn)
        main_layout.addWidget(self.game_helper_btn)
        main_layout.addWidget(self.pin_btn)

        # 设置布局
        self.setLayout(main_layout)

        # 连接游戏助手按钮的点击事件
        self.game_helper_btn.clicked.connect(self.on_game_helper_clicked)

        # 初始化时固定在屏幕顶部居中
        QTimer.singleShot(100, self.move_to_top_center)
    
    def on_game_helper_clicked(self):
        """AI助手按钮点击处理"""
        # 触发显示日志窗口
        if hasattr(self, 'main_app') and hasattr(self.main_app, 'show_log_window'):
            self.main_app.show_log_window()

    def mousePressEvent(self, event):
        """鼠标按下事件 - 现在允许按钮点击但不拖动"""
        if event.button() == Qt.MouseButton.LeftButton:
            # 检查是否点击了按钮区域
            pos = event.position().toPoint()
            for btn in [self.start_btn, self.stop_btn, self.game_helper_btn, self.pin_btn]:
                if btn.geometry().contains(btn.mapFromParent(pos)):
                    # 如果点击的是按钮，不记录位置
                    return
            # 只有点击空白区域才可能触发拖动
            pass

    def mouseMoveEvent(self, event):
        """鼠标移动事件 - 现在不响应拖动"""
        if event.buttons() == Qt.MouseButton.LeftButton:
            # 不再响应任何拖动事件
            pass

    def mouseReleaseEvent(self, event):
        """鼠标释放事件 - 确保悬浮球不隐藏"""
        # 不执行任何隐藏操作，保持悬浮球显示
        pass

    def move_to_top_center(self):
        """移动到屏幕顶部居中"""
        screen = QApplication.screenAt(self.pos())
        if not screen:
            screen = QApplication.primaryScreen()

        screen_geo = screen.availableGeometry()

        # 计算居中位置
        target_x = screen_geo.left() + (screen_geo.width() - 160) // 2
        target_y = screen_geo.top() + 10

        # 创建动画
        if hasattr(self, 'move_animation') and self.move_animation is not None:
            self.move_animation.stop()

        self.move_animation = QPropertyAnimation(self, b"pos")
        self.move_animation.setDuration(300)
        self.move_animation.setEndValue(QPoint(target_x, target_y))
        self.move_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.move_animation.start()

    def toggle_hide(self):
        """切换隐藏状态"""
        pass

    def paintEvent(self, event):
        """绘制玻璃背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 圆角矩形路径
        rect_path = QPainterPath()
        rect_path.addRoundedRect(0, 0, self.width(), self.height(), 15, 15)

        # 半透明白色基底
        painter.fillPath(rect_path, QColor(255, 255, 255, 40))

        # 线性渐变营造光泽感
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(255, 255, 255, 160))
        gradient.setColorAt(0.5, QColor(255, 255, 255, 60))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 160))
        painter.fillPath(rect_path, QBrush(gradient))

        # 半透明边框
        pen = QPen(QColor(255, 255, 255, 80))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawPath(rect_path)

class LogWindow(QMainWindow):
    """日志窗口类"""

    minimize_to_ball_signal = pyqtSignal()

    def __init__(self, floating_ball):
        super().__init__()
        self.floating_ball = floating_ball
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('AI对话')
        self.setFixedSize(400, 600)

        # 设置窗口标志为无边框、背景透明
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 设置在所有桌面层上（不被其他应用覆盖）
        if hasattr(Qt.WindowType, 'WindowDoesNotAcceptFocus'):
            self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)

        # 创建中心部件和布局
        central_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)

        # ========== 顶部按钮栏 ==========
        top_layout = QHBoxLayout()
        top_layout.addStretch()

        # 关闭按钮
        close_btn = QPushButton("×")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet(CLOSE_BTN_STYLE)
        close_btn.clicked.connect(self.close_window)

        top_layout.addWidget(close_btn)
        main_layout.addLayout(top_layout)

        # ========== 对话显示区域 ==========
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setFont(QFont('Consolas', 10))
        self.chat_display.setStyleSheet("""
            QTextEdit {
                background-color: rgba(255, 255, 255, 30);
                color: #2C3E50;
                border: 1px solid rgba(255, 255, 255, 80);
                border-radius: 10px;
                padding: 10px;
            }
        """)

        # 添加欢迎消息
        self.add_message("AI", "你好！我是你的 AI 助手，有什么问题可以帮助你吗？")

        main_layout.addWidget(self.chat_display)

        # ========== 输入区域 ==========
        input_layout = QHBoxLayout()

        # 输入框
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入问题...")
        self.input_field.setFont(QFont('Microsoft YaHei', 10))
        self.input_field.setStyleSheet("""
            QLineEdit {
                background-color: rgba(255, 255, 255, 50);
                border: 1px solid rgba(255, 255, 255, 80);
                border-radius: 15px;
                padding: 8px 15px;
                color: #2C3E50;
            }
            QLineEdit:focus {
                border: 1px solid #4361EE;
            }
        """)
        self.input_field.returnPressed.connect(self.send_message)

        # 发送按钮
        send_btn = QPushButton('发送')
        send_btn.setFixedSize(80, 35)
        send_btn.setStyleSheet(SEND_BTN_STYLE)
        send_btn.clicked.connect(self.send_message)

        input_layout.addWidget(self.input_field)
        input_layout.addWidget(send_btn)
        main_layout.addLayout(input_layout)

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def add_message(self, sender, message):
        """添加消息"""
        if sender == "AI":
            self.chat_display.append(f'<b style="color: #4361EE;">AI:</b> {message}')
        else:
            self.chat_display.append(f'<b style="color: #27AE60;">你:</b> {message}')
        # 滚动到底部
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def send_message(self):
        """发送消息"""
        message = self.input_field.text().strip()
        if message:
            # 显示用户消息
            self.add_message("User", message)
            # 清空输入框
            self.input_field.clear()

            # 模拟 AI 回复（这里可以替换为真实的 API 调用）
            ai_response = """爆炎树位于璃月翠玦坡西南的洞穴内，挑战需消耗 40 树脂，核心打法是用水元素击破核心使其瘫痪，再全力输出。

一、配队推荐
队伍需包含水元素破核角色、主 C、生存位与增伤位。
破核：行秋、芭芭拉、夜兰、心海（水元素破核效率最高）
主 C：胡桃、神里绫华、刻晴、甘雨
生存：钟离、班尼特、七七
增伤：万叶、砂糖、珐露珊

二、战斗流程
开场破根核：贴近爆炎树根部，用水元素快速击破核心，核心破碎后爆炎树会倒地瘫痪，此时切换主 C 全力爆发输出。
转阶段破头核：爆炎树复苏后，核心转移至头部花冠，用远程水元素攻击击破头核，使其再次瘫痪，重复破核、输出循环。
全程走位：始终贴紧爆炎树根部绕圈移动，可规避多数攻击。

三、技能规避
烈焰头槌 / 三连头槌：树干发光时，立即冲刺后撤躲避。
横扫 / 旋转攻击：看到爆炎树扭动身体，快速远离或绕到身后。
追踪火球 / 连珠火球：持续绕树跑动，临近时冲刺闪避。
地火喷射：贴近树根绕圈，远离会让地火更密集。
烈焰种子：发射落地后，及时用元素攻击摧毁，防止爆炸形成持续火圈。

四、注意事项
爆炎树对火元素伤害抗性极高，避免用火元素角色主 C。
核心未破时，爆炎树全伤害减免，输出极低，破核是关键。
可携带耐热药剂，降低火元素伤害，提升容错率。"""

            QTimer.singleShot(1000, lambda: self.add_message("AI", ai_response))

    def clear_log(self):
        """清空对话"""
        self.chat_display.clear()

    def minimize_to_ball(self):
        """最小化到悬浮球"""
        self.hide()
        self.floating_ball.show()
        self.floating_ball.raise_()
        self.floating_ball.move_to_top_center()

    def close_window(self):
        """关闭窗口"""
        self.hide()
        self.floating_ball.show()
        self.floating_ball.raise_()
        self.floating_ball.move_to_top_center()

    def paintEvent(self, event):
        """绘制玻璃背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 圆角矩形路径
        rect_path = QPainterPath()
        rect_path.addRoundedRect(0, 0, self.width(), self.height(), 20, 20)

        # 半透明白色基底
        painter.fillPath(rect_path, QColor(255, 255, 255, 40))

        # 线性渐变营造光泽感
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(255, 255, 255, 160))
        gradient.setColorAt(0.5, QColor(255, 255, 255, 60))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 160))
        painter.fillPath(rect_path, QBrush(gradient))

        # 半透明边框
        pen = QPen(QColor(255, 255, 255, 80))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawPath(rect_path)

    def mousePressEvent(self, event):
        """鼠标按下事件 - 实现拖动"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """鼠标移动事件 - 实现拖动"""
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        event.accept()


class MainApp(QObject):
    """主应用程序类"""

    def __init__(self):
        super().__init__()
        self.app = QApplication(sys.argv)

        # 创建悬浮球
        self.floating_ball = FloatingBall()
        # 将main_app引用传递给悬浮球，以便按钮可以访问
        self.floating_ball.main_app = self

        # 创建日志窗口（初始隐藏）
        self.log_window = LogWindow(self.floating_ball)

        # 连接信号
        self.setup_connections()

        # 显示悬浮球
        self.floating_ball.show()

        # 定时器用于检测点击（避免拖动时触发）
        self.click_timer = QTimer()
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self.show_log_window)

        self.is_dragging = False
        
    def show_log_window(self, keep_ball_visible=False):
        """显示日志窗口"""
        if not keep_ball_visible:
            self.floating_ball.hide()

        # 让日志窗口出现在悬浮球下方
        ball_pos = self.floating_ball.pos()
        screen = QApplication.screenAt(ball_pos)
        if not screen:
            screen = QApplication.primaryScreen()

        screen_geo = screen.availableGeometry()

        # 窗口出现在悬浮球正下方
        x = ball_pos.x() - 200
        y = ball_pos.y() + 70

        # 确保窗口在屏幕内
        if x < screen_geo.left():
            x = screen_geo.left()
        if x + 400 > screen_geo.right():
            x = screen_geo.right() - 400
        if y + 600 > screen_geo.bottom():
            y = screen_geo.bottom() - 600

        self.log_window.move(x, y)
        self.log_window.show()
        self.log_window.activateWindow()
        self.log_window.raise_()

    def setup_connections(self):
        """设置信号连接"""
        # 使用事件过滤器来区分点击和拖动
        self.floating_ball.installEventFilter(self)

    def eventFilter(self, obj, event):
        """事件过滤器"""
        from PyQt6.QtCore import QEvent

        if obj == self.floating_ball:
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.click_timer.start(150)
                    self.is_dragging = False
            elif event.type() == QEvent.Type.MouseMove:
                if event.buttons() == Qt.MouseButton.LeftButton:
                    self.is_dragging = True
                    self.click_timer.stop()
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    if not self.is_dragging and self.click_timer.isActive():
                        self.click_timer.stop()
                        self.show_log_window()

        return False

    def run(self):
        """运行应用"""
        sys.exit(self.app.exec())


