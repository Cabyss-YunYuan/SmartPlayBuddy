import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QTextEdit, QFrame, \
    QHBoxLayout, QLineEdit, QLabel
from PyQt6.QtCore import Qt, QPoint, QTimer, QPropertyAnimation, QEasingCurve, QDateTime
from PyQt6.QtGui import QFont, QPainter, QColor, QBrush, QPen, QLinearGradient, QPainterPath, QFontMetrics, QCursor


class _HintLineWidget(QWidget):
    """悬浮球隐藏后的位置提示线条"""

    def __init__(self, width, height):
        super().__init__()
        self.setFixedSize(width, height)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        radius = self.height() / 2
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), radius, radius)

        painter.fillPath(path, QBrush(QColor(210, 228, 248, 145)))


class FloatingBall(QFrame):
    """悬浮球类"""

    def __init__(self, log_window):
        super().__init__()
        self.log_window = log_window
        self._target_pos = None
        self._is_ball_visible = True
        self._hover_timer = None
        self._poll_timer = None
        self._trigger_window = None
        self._hint_line = None
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
        QTimer.singleShot(100, self._move_to_top_center_and_start)

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
        """窗口关闭时停止并清理所有定时器"""
        if hasattr(self, 'time_timer') and self.time_timer is not None:
            self.time_timer.stop()
            self.time_timer.deleteLater()
            self.time_timer = None
        if self._hover_timer is not None:
            self._hover_timer.stop()
            self._hover_timer.deleteLater()
            self._hover_timer = None
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer.deleteLater()
            self._poll_timer = None
        if self._trigger_window is not None:
            self._trigger_window.close()
            self._trigger_window = None
        if self._hint_line is not None:
            self._hint_line.close()
            self._hint_line.deleteLater()
            self._hint_line = None
        super().closeEvent(event)

    def mousePressEvent(self, event):
        """鼠标按下事件 - 不再响应"""
        pass

    def _move_to_top_center_and_start(self):
        """移动到屏幕顶部居中，并启动自动隐藏倒计时"""
        self._move_to_top_center(animated=False)
        self._start_auto_hide_timer()

    def _move_to_top_center(self, animated=True):
        """移动到屏幕顶部居中，记录目标位置"""
        screen = QApplication.screenAt(self.pos()) if animated else QApplication.primaryScreen()
        if not screen:
            screen = QApplication.primaryScreen()

        screen_geo = screen.availableGeometry()

        target_x = screen_geo.left() + (screen_geo.width() - self.width()) // 2
        target_y = screen_geo.top() + 10
        self._target_pos = QPoint(target_x, target_y)

        if animated:
            if hasattr(self, 'move_animation') and self.move_animation is not None:
                self.move_animation.stop()

            self.move_animation = QPropertyAnimation(self, b"pos")
            self.move_animation.setDuration(300)
            self.move_animation.setEndValue(self._target_pos)
            self.move_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.move_animation.start()
        else:
            self.move(target_x, target_y)

    def _start_auto_hide_timer(self):
        """启动 4 秒自动隐藏倒计时"""
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._hide_ball)
        self._auto_hide_timer.start(4000)

    def _hide_ball(self):
        """将悬浮球向上移出屏幕"""
        self._is_ball_visible = False
        screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        if hasattr(self, 'move_animation') and self.move_animation is not None:
            self.move_animation.stop()

        self.move_animation = QPropertyAnimation(self, b"pos")
        self.move_animation.setDuration(300)
        self.move_animation.setEndValue(QPoint(self.x(), screen_geo.top() - self.height() - 5))
        self.move_animation.setEasingCurve(QEasingCurve.Type.InCubic)
        self.move_animation.finished.connect(self._on_hide_finished)
        self.move_animation.start()

    def _on_hide_finished(self):
        """隐藏动画完成后，显示提示线条并启动触发窗口轮询"""
        try:
            self.move_animation.finished.disconnect(self._on_hide_finished)
        except TypeError:
            pass
        self._show_hint_line()
        self._setup_trigger_window()
        self._start_polling()

    def _show_ball(self):
        """将悬浮球从屏幕顶部向下滑出，恢复到初始位置"""
        if self._is_ball_visible or self._target_pos is None:
            return
        self._is_ball_visible = True
        self._stop_polling()
        self._hide_hint_line()

        if hasattr(self, 'move_animation') and self.move_animation is not None:
            self.move_animation.stop()

        self.move_animation = QPropertyAnimation(self, b"pos")
        self.move_animation.setDuration(300)
        self.move_animation.setEndValue(self._target_pos)
        self.move_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.move_animation.finished.connect(self._on_show_finished)
        self.move_animation.start()

    def _on_show_finished(self):
        """显示动画完成后，清理并重启自动隐藏倒计时"""
        try:
            self.move_animation.finished.disconnect(self._on_show_finished)
        except TypeError:
            pass
        self._teardown_trigger_window()
        self._start_auto_hide_timer()

    def _setup_trigger_window(self):
        """创建屏幕顶部透明触发窗口，用于检测鼠标进入该区域"""
        if self._trigger_window is not None:
            return

        screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        self._trigger_window = QWidget()
        self._trigger_window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput
        )
        self._trigger_window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._trigger_window.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        trigger_height = 6
        trigger_x = screen_geo.left() + (screen_geo.width() - self.width()) // 2
        self._trigger_window.setGeometry(trigger_x, screen_geo.top(), self.width(), trigger_height)
        self._trigger_window.show()

    def _teardown_trigger_window(self):
        """销毁触发窗口"""
        if self._trigger_window is not None:
            self._trigger_window.close()
            self._trigger_window.deleteLater()
            self._trigger_window = None

    def _start_polling(self):
        """启动光标位置轮询，检测光标是否进入触发区域"""
        if self._hover_timer is None:
            self._hover_timer = QTimer(self)
            self._hover_timer.setSingleShot(True)
            self._hover_timer.setInterval(300)
            self._hover_timer.timeout.connect(self._show_ball)

        if self._poll_timer is None:
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(100)
            self._poll_timer.timeout.connect(self._check_cursor_in_trigger_zone)

        self._poll_timer.start()

    def _stop_polling(self):
        """停止光标轮询和悬停计时"""
        if self._poll_timer is not None:
            self._poll_timer.stop()
        if self._hover_timer is not None:
            self._hover_timer.stop()

    def _check_cursor_in_trigger_zone(self):
        """检测光标是否在触发窗口区域内"""
        if self._is_ball_visible or self._trigger_window is None:
            return
        cursor_pos = QCursor.pos()
        trigger_rect = self._trigger_window.geometry()
        if trigger_rect.contains(cursor_pos):
            if not self._hover_timer.isActive():
                self._hover_timer.start()
        else:
            self._hover_timer.stop()

    def _show_hint_line(self):
        """在屏幕顶部显示悬浮球隐藏位置提示线条"""
        if self._hint_line is not None:
            return
        hint_height = max(2, self.height() // 8)
        self._hint_line = _HintLineWidget(self.width(), hint_height)
        screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()
        hint_x = screen_geo.left() + (screen_geo.width() - self.width()) // 2
        self._hint_line.move(hint_x, screen_geo.top() + 2)
        self._hint_line.show()

    def _hide_hint_line(self):
        """隐藏并销毁提示线条"""
        if self._hint_line is not None:
            self._hint_line.close()
            self._hint_line.deleteLater()
            self._hint_line = None

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
