# 骨架模板与验收清单

## A. CLI 文本游戏骨架（默认）

```python
import random

def main():
    print("=== 游戏标题 ===")
    print("规则说明")
    # 初始化状态
    while True:
        # 渲染当前状态
        # 游戏结束判定: break
        cmd = input("> ").strip().lower()
        if cmd in ("quit", "q", "退出"):
            break
        # 解析并执行命令（非法输入只提示，不崩溃）
    print("游戏结束，谢谢游玩")

if __name__ == "__main__":
    main()
```

要点：
- 输入统一 `.strip().lower()` 再判断；未知命令回复帮助提示。
- 用 `random` 等标准库即可，不要引入第三方依赖。

## B. pygame 图形游戏骨架

```
games/<名字>/
├── main.py
└── requirements.txt   # 内容: pygame
```

```python
import pygame

def main():
    pygame.init()
    screen = pygame.display.set_mode((640, 480))
    clock = pygame.time.Clock()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
        # 更新逻辑
        screen.fill((0, 0, 0))
        # 绘制
        pygame.display.flip()
        clock.tick(60)
    pygame.quit()

if __name__ == "__main__":
    main()
```

## C. HTML5 + JS 网页游戏骨架

```
games/<名字>/index.html   # 单文件，双击即玩，无需服务器
```

要点：
- 游戏循环用 `requestAnimationFrame`；键盘监听 `keydown/keyup`；用 `<canvas>` 或 DOM 渲染。
- 提供"重新开始"按钮与暂停（P）。
- 不引外部 CDN，保证离线可玩。

## 验收清单（每次交付前逐项确认）
- [ ] 启动方式已写明（命令或双击打开），无需联网
- [ ] 合法操作可完成一局并到达明确结束状态
- [ ] 非法输入/操作只提示，不崩溃
- [ ] 存在明确的退出方式（quit / Ctrl+C / 关闭窗口 / ESC）
- [ ] 界面语言符合用户要求（默认中文）
- [ ] CLI 形态已完成模拟输入冒烟测试
