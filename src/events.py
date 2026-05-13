import pygame

handlers = {
    pygame.QUIT: lambda event: exit()
}

def handle_events():
    used_events = [i for i in handlers.keys() if isinstance(i,int)]
    events = pygame.event.get(used_events)
    for event in events:
        handlers[event.type](event)

    handlers['draw']()
    pygame.display.flip()

def draw(func):
    handlers['draw'] = func

def event(type: int):
    def decorator(func):
        handlers[type] = func
    return decorator
