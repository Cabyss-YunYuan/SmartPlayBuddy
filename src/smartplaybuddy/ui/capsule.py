import sys
import ctypes
from PyQt6.QtWidgets import QApplication, QFrame, QToolTip
from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty, QDateTime, QEvent
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFont, QFontMetrics, QFontMetricsF, QPainterPath, QCursor

if sys.platform == "win32":
    from ctypes import wintypes

    WH_MOUSE_LL = 14
    HC_ACTION = 0
    WM_LBUTTONDOWN = 0x0201
    WM_MBUTTONDOWN = 0x0207
    WM_RBUTTONDOWN = 0x0204

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p
    )


class FloatingBall(QFrame):
    """悬浮球类 - 深灰色圆形小球，悬停渐变为胶囊形显示时间，右键展开功能面板"""

    def __init__(self, log_window):
        super().__init__()
        self.log_window = log_window
        self._drag_pos = None
        self._expanded = False
        self._anim_progress = 0.0
        self._center_x = 0
        self._animation = None
        self._time_text = ""
        self._mouse_hook = None
        self._panel_padding_y = 0
        self._expand_timer = QTimer(self)
        self._expand_timer.setSingleShot(True)
        self._expand_timer.setInterval(300)
        self._expand_timer.timeout.connect(self._expand)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(3500)
        self._collapse_timer.timeout.connect(self._collapse)
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.setInterval(1000)
        self._tooltip_timer.timeout.connect(self._show_tooltip)
        self._tooltip_pos = QPoint()
        self._panel_visible = False
        self._panel_progress = 0.0
        self._panel_height = 0
        self._panel_animating = False
        self._panel_animation = None
        self._collapse_after_panel = False
        self._panel_pending = False
        self._locked = False
        self._panel_dir = "down"
        self._center_y = 0
        self._ball_ox = 0
        self._ball_oy = 0
        self._ball_w = 0
        self._panel_width = 0
        self._drag_moved = False
        self._snap_progress = 0.0
        self._snap_from = (0, 0)
        self._snap_to = (0, 0)
        self._snap_animation = None
        self._snap_margin = 4
        self._hovered_button = -1
        self._panel_buttons = [
            {"icon": "power", "tooltip": "停止", "callback": self._release_event_lock},
            {"icon": "monitor", "tooltip": "显示主界面", "callback": self._show_main_window},
        ]
        self._panel_btn_height = 0
        self._panel_btn_gap = 0
        self.init_ui()

    def _calc_panel_layout(self):
        self._panel_btn_diameter = int(self._base_size * 1.2)
        self._panel_btn_gap = int(self._base_size * 0.4)
        self._panel_gap = int(self._base_size * 0.2)
        padding = int(self._base_size * 0.4)
        self._max_panel_height = padding * 2 + self._panel_btn_diameter
        self._max_panel_width = self._max_panel_height
        inner_height = self._max_panel_height - self._panel_gap
        self._panel_padding_y = (inner_height - self._panel_btn_diameter) // 2
        self._snap_margin = max(4, int(self._base_size * 0.2))

    def init_ui(self):
        screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        self._base_size = int(min(screen_geo.width(), screen_geo.height()) * 0.04)
        self._expanded_width = int(self._base_size * 4.0)
        self._calc_panel_layout()

        self.setFixedSize(self._base_size, self._base_size)
        self.setWindowTitle('悬浮球')

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        if sys.platform == "win32":
            QTimer.singleShot(0, self._set_topmost)
            QApplication.instance().focusWindowChanged.connect(self._on_focus_changed)
            self._install_mouse_hook()

        half = self._base_size // 2
        self._center_x = screen_geo.left() + screen_geo.width() // 2
        self._center_y = screen_geo.top() + half + self._snap_margin
        self._apply_geometry()

        self._time_timer = QTimer(self)
        self._time_timer.timeout.connect(self._update_time)
        self._time_timer.start(1000)
        self._update_time()

    # ── 形状展开动画 ──

    def _get_anim_progress(self):
        return self._anim_progress

    def _set_anim_progress(self, value):
        self._anim_progress = value
        self._apply_geometry()

    anim_progress = pyqtProperty(float, fget=_get_anim_progress, fset=_set_anim_progress)

    # ── 面板展开动画 ──

    def _get_panel_progress(self):
        return self._panel_progress

    def _set_panel_progress(self, value):
        self._panel_progress = value
        self._apply_geometry()

    panel_progress = pyqtProperty(float, fget=_get_panel_progress, fset=_set_panel_progress)

    # ── 几何计算（支持四方向面板）──

    def _apply_geometry(self):
        B = self._base_size
        ball_w = int(B + (self._expanded_width - B) * self._anim_progress)
        d = self._panel_dir
        self._ball_w = ball_w

        if self._panel_progress > 0.001:
            span = self._expanded_width
            thick = int(self._max_panel_height * self._panel_progress)
            gap = self._panel_gap
        else:
            span = 0
            thick = 0
            gap = 0

        if d == "down":
            widget_w = max(ball_w, span)
            widget_h = B + gap + thick
            ball_ox = (widget_w - ball_w) // 2
            ball_oy = 0
        elif d == "up":
            widget_w = max(ball_w, span)
            widget_h = B + gap + thick
            ball_ox = (widget_w - ball_w) // 2
            ball_oy = gap + thick
        elif d == "right":
            widget_w = ball_w + gap + span
            widget_h = max(B, thick)
            ball_ox = 0
            ball_oy = (widget_h - B) // 2
        else:
            widget_w = ball_w + gap + span
            widget_h = max(B, thick)
            ball_ox = gap + span
            ball_oy = (widget_h - B) // 2

        self._ball_ox = ball_ox
        self._ball_oy = ball_oy
        self.setFixedSize(max(1, widget_w), max(1, widget_h))
        edge = self._snapped_edge()
        if edge == "left":
            ball_screen_x = self._center_x - B // 2
        elif edge == "right":
            ball_screen_x = self._center_x + B // 2 - ball_w
        else:
            ball_screen_x = self._center_x - ball_w // 2
        ball_screen_y = self._center_y - B // 2
        self.move(ball_screen_x - ball_ox, ball_screen_y - ball_oy)
        self.update()

    def _snapped_edge(self):
        avail = self._current_screen().availableGeometry()
        half_w = self._base_size // 2
        m = self._snap_margin
        tol = 2
        cx = self._center_x
        if cx <= avail.left() + half_w + m + tol:
            return "left"
        if cx >= avail.left() + avail.width() - half_w - m - tol:
            return "right"
        return None

    def _compute_panel_dir(self):
        avail = self._current_screen().availableGeometry()
        cx, cy = self._center_x, self._center_y
        edge = self._snapped_edge()
        if edge == "left":
            return "right"
        if edge == "right":
            return "left"
        need_v = self._base_size // 2 + self._panel_gap + self._max_panel_height
        need_h = self._expanded_width // 2 + self._panel_gap + self._expanded_width
        if avail.bottom() - cy >= need_v:
            return "down"
        if cy - avail.top() >= need_v:
            return "up"
        if avail.right() - cx >= need_h:
            return "right"
        if cx - avail.left() >= need_h:
            return "left"
        return "down"

    # ── 边缘吸附 ──

    def _get_snap_progress(self):
        return self._snap_progress

    def _set_snap_progress(self, value):
        self._snap_progress = value
        fx, fy = self._snap_from
        tx, ty = self._snap_to
        self._center_x = int(fx + (tx - fx) * value)
        self._center_y = int(fy + (ty - fy) * value)
        self._apply_geometry()

    snap_progress = pyqtProperty(float, fget=_get_snap_progress, fset=_set_snap_progress)

    def _current_screen(self):
        pos = QPoint(int(self._center_x), int(self._center_y))
        screen = QApplication.screenAt(pos)
        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()
        return screen

    def _snap_to_edge(self):
        avail = self._current_screen().availableGeometry()
        half = self._base_size // 2
        m = self._snap_margin
        left = avail.left()
        top = avail.top()
        right = left + avail.width()
        bottom = top + avail.height()
        W = avail.width()
        H = avail.height()
        cx, cy = self._center_x, self._center_y

        min_cx = left + half + m
        max_cx = right - half - m
        min_cy = top + half + m
        max_cy = bottom - half - m
        if min_cx > max_cx:
            min_cx = max_cx = (left + right) // 2
        if min_cy > max_cy:
            min_cy = max_cy = (top + bottom) // 2
        ccx = min(max(cx, min_cx), max_cx)
        ccy = min(max(cy, min_cy), max_cy)

        center_points = [
            (min_cx, top + H / 2),
            (max_cx, top + H / 2),
            (left + W / 2, min_cy),
            (left + W / 2, max_cy),
        ]
        magnet = self._base_size * 1.5
        best = min(center_points, key=lambda p: (p[0] - ccx) ** 2 + (p[1] - ccy) ** 2)
        dist = ((best[0] - ccx) ** 2 + (best[1] - ccy) ** 2) ** 0.5
        if dist <= magnet:
            target = (int(best[0]), int(best[1]))
        else:
            target = (int(ccx), int(ccy))
        if target != (cx, cy):
            self._animate_center_to(*target)

    def _animate_center_to(self, tx, ty):
        self._snap_from = (self._center_x, self._center_y)
        self._snap_to = (tx, ty)
        if self._snap_animation is not None:
            self._snap_animation.stop()
        self._snap_animation = QPropertyAnimation(self, b"snap_progress")
        self._snap_animation.setDuration(200)
        self._snap_animation.setStartValue(0.0)
        self._snap_animation.setEndValue(1.0)
        self._snap_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._snap_animation.start()

    def _get_panel_rect(self):
        if self._panel_progress <= 0.001:
            return QRectF()
        span = self._expanded_width
        thick = int(self._max_panel_height * self._panel_progress)
        if thick <= 0:
            return QRectF()
        gap = self._panel_gap
        d = self._panel_dir
        if d == "down":
            return QRectF((self.width() - span) // 2, self._ball_oy + self._base_size + gap, span, thick)
        elif d == "up":
            return QRectF((self.width() - span) // 2, 0, span, thick)
        elif d == "right":
            return QRectF(self._ball_w + gap, (self.height() - thick) // 2, span, thick)
        else:
            return QRectF(0, (self.height() - thick) // 2, span, thick)

    # ── 展开/收缩控制 ──

    def _expand(self):
        if self._expanded or self._drag_pos is not None:
            return
        self._panel_pending = False
        self._expanded = True
        self._run_animation(1.0)

    def _collapse(self):
        self._panel_pending = False
        if self._panel_visible:
            self._hide_panel()
            self._collapse_timer.start()
            return
        if not self._expanded or self._locked:
            return
        self._expanded = False
        self._run_animation(0.0)

    def _run_animation(self, target):
        if self._animation is not None:
            self._animation.stop()
        self._animation = QPropertyAnimation(self, b"anim_progress")
        self._animation.setDuration(300)
        self._animation.setStartValue(self._anim_progress)
        self._animation.setEndValue(target)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.finished.connect(self._on_expand_anim_finished)
        self._animation.start()

    def _on_expand_anim_finished(self):
        if self._panel_pending and self._expanded:
            self._panel_pending = False
            self._start_panel_animation(1.0)

    # ── 面板控制 ──

    def _toggle_panel(self):
        if self._panel_animating:
            return
        if self._panel_visible:
            self._hide_panel()
        else:
            self._show_panel()

    def _show_panel(self):
        self._panel_visible = True
        self._collapse_after_panel = False
        self._collapse_timer.stop()
        self._panel_dir = self._compute_panel_dir()
        if not self._expanded:
            self._expanded = True
            self._panel_pending = True
            self._run_animation(1.0)
        else:
            self._start_panel_animation(1.0)

    def _hide_panel(self):
        self._panel_visible = False
        self._start_panel_animation(0.0)

    def _start_panel_animation(self, target):
        if self._panel_animation is not None:
            self._panel_animation.stop()
        self._panel_animating = True
        self._panel_animation = QPropertyAnimation(self, b"panel_progress")
        self._panel_animation.setDuration(300)
        self._panel_animation.setStartValue(self._panel_progress)
        self._panel_animation.setEndValue(target)
        self._panel_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._panel_animation.finished.connect(self._on_panel_anim_finished)
        self._panel_animation.start()

    def _on_panel_anim_finished(self):
        self._panel_animating = False
        try:
            self._panel_animation.finished.disconnect(self._on_panel_anim_finished)
        except TypeError:
            pass
        if self._panel_progress < 0.01:
            self._panel_progress = 0.0
            self._apply_geometry()
            if self._collapse_after_panel:
                self._collapse_after_panel = False
                if self._expanded and self._drag_pos is None and not self._locked:
                    self._expanded = False
                    self._run_animation(0.0)

    # ── 按钮布局 ──

    def _get_button_rects(self):
        panel = self._get_panel_rect()
        if panel.isNull() or panel.width() <= 0 or panel.height() <= 0:
            return []
        n = len(self._panel_buttons)
        d = self._panel_btn_diameter
        gapb = self._panel_btn_gap
        total_w = n * d + (n - 1) * gapb
        start_x = panel.x() + (panel.width() - total_w) / 2
        y = panel.y() + (panel.height() - d) / 2
        rects = []
        for i in range(n):
            x = start_x + i * (d + gapb)
            rects.append(QRectF(x, y, d, d))
        return rects

    def _update_button_hover(self, pos):
        if self._drag_pos is not None or self._panel_progress < 0.1:
            return
        old = self._hovered_button
        self._hovered_button = -1
        for i, rect in enumerate(self._get_button_rects()):
            if rect.contains(pos):
                self._hovered_button = i
                break
        if self._hovered_button != old:
            self.update()
            self._tooltip_timer.stop()
            QToolTip.hideText()
            if self._hovered_button >= 0:
                rect = self._get_button_rects()[self._hovered_button]
                self._tooltip_pos = self.mapToGlobal(
                    QPoint(int(rect.center().x()), int(rect.bottom()) + 8)
                )
                self._tooltip_timer.start()

    def _show_tooltip(self):
        if self._hovered_button >= 0:
            tip = self._panel_buttons[self._hovered_button]["tooltip"]
            QToolTip.showText(self._tooltip_pos, tip, self)

    # ── 鼠标事件 ──

    def enterEvent(self, event):
        self._collapse_timer.stop()
        self._collapse_after_panel = False
        self._expand_timer.start()

    def leaveEvent(self, event):
        self._expand_timer.stop()
        self._tooltip_timer.stop()
        QToolTip.hideText()
        self._hovered_button = -1
        self.update()
        if self._drag_pos is None and not self._locked:
            self._collapse_timer.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._hovered_button >= 0:
                return
            if self._snap_animation is not None:
                self._snap_animation.stop()
            self._drag_moved = False
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
        elif event.button() == Qt.MouseButton.RightButton:
            self._toggle_panel()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self._toggle_lock()

    def _toggle_lock(self):
        self._locked = not self._locked
        if self._locked:
            self._expand_timer.stop()
            self._collapse_timer.stop()
            if not self._expanded:
                self._expanded = True
                self._run_animation(1.0)
        else:
            if not self.underMouse() and not self._panel_visible:
                self._collapse_timer.start()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._expanded and self._hovered_button < 0:
                self._release_event_lock()

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate:
            self._collapse_now()
        return super().event(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            new_topleft = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_topleft)
            self._center_x = new_topleft.x() + self._ball_ox + self._ball_w // 2
            self._center_y = new_topleft.y() + self._ball_oy + self._base_size // 2
            self._drag_moved = True
        else:
            self._update_button_hover(event.position())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._hovered_button >= 0:
                rect = self._get_button_rects()[self._hovered_button]
                if rect.contains(event.position()):
                    cb = self._panel_buttons[self._hovered_button]["callback"]
                    if cb:
                        cb()
                self._hovered_button = -1
                self.update()
                return
            was_drag = self._drag_moved
            self._drag_moved = False
            self._drag_pos = None
            if was_drag:
                self._snap_to_edge()
            if not self.rect().contains(event.position().toPoint()) and not self._locked:
                self._collapse_timer.start()

    # ── 绘制 ──

    def _update_time(self):
        self._time_text = QDateTime.currentDateTime().toString("HH:mm:ss")
        if self._anim_progress > 0:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        B = self._base_size
        ball_w = self._ball_w
        bx = self._ball_ox
        by = self._ball_oy
        margin = 1
        rx = (B - 2 * margin) / 2

        painter.setBrush(QBrush(QColor(0, 0, 0, 204)))
        painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
        painter.drawRoundedRect(bx + margin, by + margin, ball_w - 2 * margin, B - 2 * margin, rx, rx)

        if self._anim_progress > 0.1 and self._time_text:
            self._draw_time(painter, QRectF(bx, by, ball_w, B))

        if self._panel_progress > 0.01:
            self._draw_panel(painter)

    def _draw_time(self, painter, ball_rect):
        w = ball_rect.width()
        h = ball_rect.height()
        padding = int(h * 0.3)
        available_width = w - 2 * padding
        font_size = max(10, int(h * 0.42))

        font = QFont()
        font.setBold(True)
        sample = "00:00:00"

        while font_size > 8:
            font.setPointSize(font_size)
            metrics = QFontMetricsF(font)
            if metrics.horizontalAdvance(sample) <= available_width:
                break
            font_size -= 1

        font.setPointSize(font_size)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(ball_rect, Qt.AlignmentFlag.AlignCenter, self._time_text)

    def _draw_panel(self, painter):
        panel = self._get_panel_rect()
        if panel.isNull():
            return
        rx = min(panel.width(), panel.height()) / 2

        painter.setBrush(QBrush(QColor(0, 0, 0, 204)))
        painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
        painter.drawRoundedRect(panel, rx, rx)

        for i, rect in enumerate(self._get_button_rects()):
            is_hover = (i == self._hovered_button)
            icon_type = self._panel_buttons[i]["icon"]
            center = rect.center()
            r = rect.width() / 2

            painter.setPen(Qt.PenStyle.NoPen)
            if is_hover:
                if icon_type == "power":
                    painter.setBrush(QBrush(QColor(255, 59, 48, 235)))
                else:
                    painter.setBrush(QBrush(QColor(10, 132, 255, 235)))
            else:
                painter.setBrush(QBrush(QColor(255, 255, 255, 46)))
            painter.drawEllipse(center, r, r)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            if is_hover:
                border_alpha = 60
            else:
                border_alpha = 28
            painter.setPen(QPen(QColor(255, 255, 255, border_alpha), 1))
            painter.drawEllipse(QPointF(center.x(), center.y()), r - 0.5, r - 0.5)

            icon_color = QColor(255, 255, 255) if is_hover else QColor(235, 235, 240)
            self._draw_icon(painter, icon_type, rect, icon_color)

    def _draw_icon(self, painter, icon_type, rect, color):
        cx, cy = rect.center().x(), rect.center().y()
        d = rect.width()
        s = d * 0.25

        pen = QPen(color, max(1.5, d * 0.06), cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if icon_type == "monitor":
            sw, sh = s * 1.3, s * 0.9
            screen_rect = QRectF(cx - sw, cy - sh - s * 0.2, sw * 2, sh * 2)
            painter.drawRoundedRect(screen_rect, s * 0.15, s * 0.15)
            painter.drawLine(
                QPointF(cx, screen_rect.bottom()),
                QPointF(cx, screen_rect.bottom() + s * 0.35)
            )
            painter.drawLine(
                QPointF(cx - s * 0.45, screen_rect.bottom() + s * 0.35),
                QPointF(cx + s * 0.45, screen_rect.bottom() + s * 0.35)
            )

        elif icon_type == "power":
            r = s * 0.85
            painter.drawArc(
                QRectF(cx - r, cy - r + s * 0.15, r * 2, r * 2),
                225 * 16, -270 * 16
            )
            painter.drawLine(
                QPointF(cx, cy - r + s * 0.05),
                QPointF(cx, cy - s * 0.15)
            )

    # ── 功能按钮回调 ──

    def _show_main_window(self):
        if self.log_window is not None:
            self.log_window.show()
            self.log_window.raise_()
            self.log_window.activateWindow()

    def _release_event_lock(self):
        from . import client
        if client is not None and client.connected:
            import asyncio
            loop = asyncio.get_event_loop()
            loop.create_task(client.revoke_permit())

    # ── 置顶 ──

    def _on_focus_changed(self, window):
        if window is not None:
            self._set_topmost()

    def _install_mouse_hook(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        try:
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_ulong,
            ]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            user32.CallNextHookEx.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ]
            self._mouse_hook_proc = HOOKPROC(self._ll_mouse_proc)
            h_module = kernel32.GetModuleHandleW(None)
            self._mouse_hook = user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._mouse_hook_proc, h_module, 0,
            )
        except Exception:
            self._mouse_hook = None

    def _ll_mouse_proc(self, nCode, wParam, lParam):
        try:
            if nCode == HC_ACTION and wParam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                if self._expanded or self._panel_visible:
                    gp = QCursor.pos()
                    if not self.frameGeometry().contains(gp):
                        QTimer.singleShot(0, self._collapse_now)
        except Exception:
            pass
        return ctypes.windll.user32.CallNextHookEx(self._mouse_hook, nCode, wParam, lParam)

    def _collapse_now(self):
        if self._drag_pos is not None or self._panel_animating:
            return
        self._panel_pending = False
        self._expand_timer.stop()
        self._collapse_timer.stop()
        self._tooltip_timer.stop()
        QToolTip.hideText()
        self._hovered_button = -1
        if self._panel_visible:
            self._collapse_after_panel = True
            self._hide_panel()
        elif self._expanded and not self._locked:
            self._expanded = False
            self._run_animation(0.0)

    def _set_topmost(self):
        hwnd = int(self.winId())
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)

    def closeEvent(self, event):
        if sys.platform == "win32" and getattr(self, "_mouse_hook", None):
            try:
                ctypes.windll.user32.UnhookWindowsHookEx(self._mouse_hook)
            except Exception:
                pass
            self._mouse_hook = None
        if self._animation is not None:
            self._animation.stop()
        if self._panel_animation is not None:
            self._panel_animation.stop()
        if self._snap_animation is not None:
            self._snap_animation.stop()
        super().closeEvent(event)
