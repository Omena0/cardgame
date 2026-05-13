from __future__ import annotations

import pygame


class TextInputVisualizer:
    def __init__(
        self,
        manager: object | None = None,
        font_object: pygame.font.Font | None = None,
        font_color: tuple[int, int, int] = (245, 245, 240),
        cursor_color: tuple[int, int, int] = (245, 245, 240),
        antialias: bool = True,
    ) -> None:
        self.manager = manager
        self.font_object = font_object or pygame.font.SysFont("DejaVu Sans", 22)
        self.font_color = font_color
        self.cursor_color = cursor_color
        self.antialias = antialias
        self.value = ""
        self.cursor_visible = True
        self._last_blink = pygame.time.get_ticks()
        self.surface = self.font_object.render("", self.antialias, self.font_color)

    def update(self, events: list[pygame.event.Event]) -> None:
        for event in events:
            if event.type != pygame.KEYDOWN:
                continue
            if event.key == pygame.K_BACKSPACE:
                self.value = self.value[:-1]
            elif event.key == pygame.K_RETURN:
                continue
            elif event.key == pygame.K_TAB:
                continue
            else:
                text = getattr(event, "unicode", "")
                if text and text.isprintable():
                    self.value += text

        now = pygame.time.get_ticks()
        if now - self._last_blink > 500:
            self._last_blink = now
            self.cursor_visible = not self.cursor_visible

        display = self.value + ("|" if self.cursor_visible else "")
        self.surface = self.font_object.render(display, self.antialias, self.font_color)
