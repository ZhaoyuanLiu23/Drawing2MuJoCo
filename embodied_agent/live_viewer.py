"""Read-only MuJoCo live viewer. No state replay and no physics UI inputs.

The execution thread updates MjvScene from its actual MjModel/MjData after every
step. The owned GUI thread renders that scene; it never steps or edits MjData.
Only camera movement and window closure are exposed. GUI imports are lazy.
"""
import threading
import time


class LiveViewer:
    def __init__(self, model, data, *, on_close):
        self.model, self.data = model, data  # Exact execution objects, not copies.
        self._on_close = on_close
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._status = "observe / initializing"
        self.error = None
        self.user_closed = False
        self._wall_anchor = time.monotonic()
        self._sim_anchor = float(data.time)
        self._last_sync = self._wall_anchor
        self._thread = threading.Thread(target=self._render_loop, name="cad-agent-viewer", daemon=False)
        self._thread.start()
        self._ready.wait()
        if self.error is not None:
            self.close()
            raise RuntimeError("MuJoCo viewer failed to open: " + str(self.error))

    def is_running(self):
        return self._thread.is_alive() and not self._stop.is_set()

    def set_status(self, skill, stage):
        with self._lock:
            self._status = str(skill) + " / " + str(stage)

    def sync(self, *, skill, stage):
        """Called on the execution thread after each real mj_step/mj_forward."""
        if not self.is_running():
            return
        with self._lock:
            self._status = str(skill) + " / " + str(stage)
            self._mj.mjv_updateScene(self.model, self.data, self._option, None,
                                     self._camera, self._mj.mjtCatBit.mjCAT_ALL, self._scene)
        # Cap fast simulations near real time without changing timestep/control.
        # Planning or rendering delays are not followed by accelerated playback.
        now, sim_time = time.monotonic(), float(self.data.time)
        if now - self._last_sync > .1:
            self._wall_anchor, self._sim_anchor = now, sim_time
        delay = self._wall_anchor + sim_time - self._sim_anchor - now
        if delay > .005:
            self._stop.wait(delay)
        self._last_sync = time.monotonic()

    def close(self):
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join()

    def _render_loop(self):
        window, context = None, None
        try:
            import glfw
            import mujoco
            self._mj = mujoco
            if not glfw.init():
                raise RuntimeError("GLFW initialization failed")
            # RGB-D creates hidden GLFW windows; explicitly override visibility.
            glfw.default_window_hints()
            glfw.window_hint(glfw.VISIBLE, glfw.TRUE)
            window = glfw.create_window(1100, 800, "MuJoCo | CAD Agent", None, None)
            if window is None:
                raise RuntimeError("GLFW could not create the viewer window")
            glfw.make_context_current(window)
            glfw.swap_interval(1)
            self._camera = mujoco.MjvCamera()
            mujoco.mjv_defaultFreeCamera(self.model, self._camera)
            self._option = mujoco.MjvOption()
            self._scene = mujoco.MjvScene(self.model, maxgeom=10000)
            context = mujoco.MjrContext(self.model, mujoco.mjtFontScale.mjFONTSCALE_150)
            # Initialization precedes task.run(), so execution data is quiescent.
            mujoco.mjv_updateScene(self.model, self.data, self._option, None,
                                   self._camera, mujoco.mjtCatBit.mjCAT_ALL, self._scene)
            previous = list(glfw.get_cursor_pos(window))

            def cursor(win, x, y):
                dx, dy = x - previous[0], y - previous[1]
                previous[:] = [x, y]
                left = glfw.get_mouse_button(win, glfw.MOUSE_BUTTON_LEFT) == glfw.PRESS
                right = glfw.get_mouse_button(win, glfw.MOUSE_BUTTON_RIGHT) == glfw.PRESS
                if not (left or right):
                    return
                height = max(1, glfw.get_window_size(win)[1])
                action = mujoco.mjtMouse.mjMOUSE_MOVE_V if right else mujoco.mjtMouse.mjMOUSE_ROTATE_V
                with self._lock:
                    mujoco.mjv_moveCamera(self.model, action, dx / height, dy / height, self._scene, self._camera)

            def scroll(win, x, y):
                with self._lock:
                    mujoco.mjv_moveCamera(self.model, mujoco.mjtMouse.mjMOUSE_ZOOM, 0, -.05 * y, self._scene, self._camera)

            def key(win, keycode, scancode, action, mods):
                if keycode == glfw.KEY_ESCAPE and action == glfw.PRESS:
                    glfw.set_window_should_close(win, True)

            glfw.set_cursor_pos_callback(window, cursor)
            glfw.set_scroll_callback(window, scroll)
            glfw.set_key_callback(window, key)
            self._ready.set()
            while not self._stop.is_set():
                glfw.poll_events()
                if glfw.window_should_close(window):
                    self.user_closed = True
                    self._stop.set()
                    self._on_close()
                    break
                width, height = glfw.get_framebuffer_size(window)
                if width and height:
                    with self._lock:
                        viewport = mujoco.MjrRect(0, 0, width, height)
                        mujoco.mjr_render(viewport, self._scene, context)
                        mujoco.mjr_overlay(mujoco.mjtFont.mjFONT_NORMAL, mujoco.mjtGridPos.mjGRID_TOPLEFT,
                                           viewport, self._status, "Live | camera only | Esc: stop", context)
                    glfw.swap_buffers(window)
                self._stop.wait(.005)
        except Exception as exc:
            self.error = exc
            self._stop.set()
            self._on_close()
        finally:
            self._ready.set()
            try:
                if context is not None:
                    context.free()
            finally:
                if window is not None:
                    glfw.destroy_window(window)
                # Do not glfw.terminate(): the RGB-D renderer owns other contexts.
