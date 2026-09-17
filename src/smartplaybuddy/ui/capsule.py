import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QTextEdit, QFrame, \
    QHBoxLayout, QLineEdit, QLabel
from PyQt6.QtCore import Qt, QPoint, QTimer, QPropertyAnimation, QEasingCurve, QDateTime
from PyQt6.QtGui import QFont, QPainter, QColor, QBrush, QPen, QLinearGradient, QPainterPath, QFontMetrics


class FloatingBall(QFrame):
    """悬浮球类"""

    def __init__(self, log_window):
        super().__init__()
        self.log_window = log_window
        self.init_ui()

    def init_ui(self):
        # 根据屏幕分辨率自适应设置悬浮球大小
        screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        # 基于屏幕宽度计算合适的尺寸（约为屏幕宽度的 15-20%）
        base_width = int(screen_geo.width() * 0.18)
        base_width = max(320, min(base_width, 500))  # 限制在 320-500 之间

        # 高度保持固定比例
        base_height = int(base_width * 0.1875)  # 保持原来的比例
        base_height = max(60, min(base_height, 100))  # 限制在 60-100 之间

        self.setFixedSize(base_width, base_height)
        self.setWindowTitle('悬浮球')

        # 移除窗口框架，设置为工具窗口
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.Tool |
                            Qt.WindowType.WindowStaysOnTopHint)

        # 设置窗口透明背景
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 始终保持在最前（包括全屏应用）
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        # 创建实时时间显示控件
        self.create_time_label()

        # 初始化时固定在屏幕顶部居中
        QTimer.singleShot(100, self.move_to_top_center)

    def create_time_label(self):
        """创建实时时间显示控件"""
        self.time_label = QLabel(self)
        self.time_label.setGeometry(0, 0, self.width(), self.height())
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.time_label.setStyleSheet("color: white; background: transparent;")

        # 根据悬浮球尺寸自适应字体大小，避免文字过小、遮挡或溢出
        self._apply_time_font()

        # 每秒刷新一次时间显示
        self.time_timer = QTimer(self)
        self.time_timer.timeout.connect(self.update_time)
        self.time_timer.start(1000)
        self.update_time()

    def _apply_time_font(self):
        """计算并设置合宜的时间字体大小"""
        # 以高度为基准的初始字号，并预留左右内边距
        padding = int(self.width() * 0.08)
        available_width = self.width() - 2 * padding
        font_size = max(12, int(self.height() * 0.5))

        font = QFont()
        font.setBold(True)
        sample_text = "00:00:00"

        # 若文字宽度超出可用空间则逐步缩小字号，防止溢出
        while font_size > 8:
            font.setPointSize(font_size)
            metrics = QFontMetrics(font)
            if metrics.horizontalAdvance(sample_text) <= available_width:
                break
            font_size -= 1

        font.setPointSize(font_size)
        self.time_label.setFont(font)

    def update_time(self):
        """更新时间显示，格式为 时:分:秒"""
        self.time_label.setText(QDateTime.currentDateTime().toString("HH:mm:ss"))

    def closeEvent(self, event):
        """窗口关闭时停止并清理时间刷新定时器"""
        if hasattr(self, 'time_timer') and self.time_timer is not None:
            self.time_timer.stop()
            self.time_timer.deleteLater()
            self.time_timer = None
        super().closeEvent(event)

    def mousePressEvent(self, event):
        """鼠标按下事件 - 不再响应"""
        pass

    def move_to_top_center(self):
        """移动到屏幕顶部居中"""
        screen = QApplication.screenAt(self.pos())
        if not screen:
            screen = QApplication.primaryScreen()

        screen_geo = screen.availableGeometry()

        # 计算居中位置
        target_x = screen_geo.left() + (screen_geo.width() - self.width()) // 2
        target_y = screen_geo.top() + 10

        # 创建动画
        if hasattr(self, 'move_animation') and self.move_animation is not None:
            self.move_animation.stop()

        self.move_animation = QPropertyAnimation(self, b"pos")
        self.move_animation.setDuration(300)
        self.move_animation.setEndValue(QPoint(target_x, target_y))
        self.move_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.move_animation.start()

    def paintEvent(self, event):
        """绘制不透明质感背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 圆角半径为高度的一半，形成完美的半圆两端
        radius = self.height() / 2

        # 圆角矩形主体路径
        rect_path = QPainterPath()
        rect_path.addRoundedRect(1, 1, self.width() - 2, self.height() - 2, radius - 1, radius - 1)

        # 不透明深色竖向渐变主体，营造高级质感
        body = QLinearGradient(0, 0, 0, self.height())
        body.setColorAt(0.0, QColor(59, 66, 82))
        body.setColorAt(1.0, QColor(37, 42, 51))
        painter.fillPath(rect_path, QBrush(body))

        # 高对比度强调色外边框，边缘清晰可见
        pen = QPen(QColor(136, 192, 208))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(rect_path)

        # 内侧高光描边，增强立体层次
        inner_path = QPainterPath()
        inner_path.addRoundedRect(3, 3, self.width() - 6, self.height() - 6, radius - 3, radius - 3)
        inner_pen = QPen(QColor(76, 86, 106))
        inner_pen.setWidth(1)
        painter.setPen(inner_pen)
        painter.drawPath(inner_path)
