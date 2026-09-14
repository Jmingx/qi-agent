"""逐帧绘制宣传动画，避免命令行中文编码与动态连线表达式问题。"""
from pathlib import Path
import math
import subprocess
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H, FPS = 1080, 1920, 30
BG, FG, MUTED = '#0B1422', '#F3F7FD', '#A6B5C9'
CYAN, PURPLE, GREEN = '#66DBF2', '#BFA5FF', '#79DEBA'
FONTS = {}


def font(size: int) -> ImageFont.FreeTypeFont:
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', size)
    return FONTS[size]


def text(d: ImageDraw.ImageDraw, s: str, y: float, size: int = 44,
         color: str = FG, x: float = 540) -> None:
    assert d.textlength(s, font=font(size)) < 980, s
    d.text((x, y), s, font=font(size), fill=color, anchor='mm')


def box(d: ImageDraw.ImageDraw, y: int, title: str, sub: str,
        active: bool = False, color: str = CYAN) -> None:
    d.rounded_rectangle((170, y, 910, y + 132), radius=24,
                        fill='#142438', outline=color if active else '#314358', width=3)
    text(d, title, y + 43, 44, color if active else FG)
    text(d, sub, y + 96, 30, MUTED)


def arrow(d: ImageDraw.ImageDraw, y1: int, y2: int, progress: float,
          color: str = CYAN) -> None:
    d.line((540, y1, 540, y2), fill='#42546B', width=4)
    d.polygon([(540, y2), (530, y2-17), (550, y2-17)], fill='#8C9FB7')
    if 0 <= progress <= 1:
        y = y1 + (y2-y1-18) * progress
        d.ellipse((529, y-11, 551, y+11), fill=color)


def frame(t: float) -> Image.Image:
    im = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(im)
    d.line((80, 128, 1000, 128), fill='#314358', width=2)
    text(d, 'qi-agent  /  事件驱动内核', 88, 30, CYAN)
    scene = 0 if t < 4 else 1 if t < 10 else 2
    start = [0, 4, 10][scene]
    u = t-start
    text(d, ['01  主循环与扩展', '02  三种分发语义', '03  跨 Agent 通信'][scene], 212, 32, MUTED)
    text(d, ['推进流程，发出事件', '通知 · 改写 · 决策', '消息经邮局异步投递'][scene], 316, 58)
    if scene == 0:
        items = [(510, 'Agent 主循环', '推进状态与调用模型、工具'),
                 (840, '生命周期事件', '例如 agent/pre-step'),
                 (1170, 'EventBus → 订阅插件', '在事件点同步调用监听器')]
        for i, (y, title, sub) in enumerate(items):
            box(d, y, title, sub, u >= i*1.0)
        arrow(d, 642, 840, (u-.6)/.8)
        arrow(d, 972, 1170, (u-1.7)/.8)
        text(d, '按优先级执行，同优先级按注册顺序', 1450, 34, MUTED)
        caption = ['核心推进任务', '关键节点发出事件', '插件在事件点响应'][min(2, int(u))]
    elif scene == 1:
        rows = [('emit', '广播通知', '监听器依次执行，忽略返回值', CYAN),
                ('waterfall', '逐层改写', '上一个返回值 → 下一个输入', PURPLE),
                ('bail', '短路决策', '首个非 None 返回即停止', GREEN)]
        for i, (name, title, sub, color) in enumerate(rows):
            y = 485 + i*330
            active = i == min(2, int(u/2))
            d.rounded_rectangle((90, y, 990, y+265), radius=25,
                                fill='#142438', outline=color if active else '#314358', width=3)
            text(d, name+'  /  '+title, y+55, 44, color)
            text(d, sub, y+116, 34)
            local = (u-i*2)/1.7
            for k in range(3):
                x = 305+k*235
                on = active and local >= k/3 and not (i==2 and k==2)
                d.ellipse((x-23, y+174, x+23, y+220), fill=color if on else '#42546B')
                if k < 2:
                    d.line((x+29, y+197, x+197, y+197), fill='#60748D', width=3)
            if i == 2 and active and local > .4:
                text(d, '停止', y+245, 24, GREEN)
        caption = ['通知不等于并行执行', '数据沿监听器链逐层改写', '拦截结果交回调用方处理'][min(2, int(u/2))]
    else:
        nodes = [(470, 'Agent A · send', 'outbox 引用中央队列'),
                 (685, '中央队列', '消息入队'),
                 (900, 'Dispatcher', '独立线程取消息，按 target 路由'),
                 (1115, 'Agent B · inbox', '接收消息'),
                 (1330, 'Agent B · drain', '由接收方取出并处理')]
        for i, (y, title, sub) in enumerate(nodes):
            box(d, y, title, sub, u >= i*.65, GREEN)
            if i < 4:
                arrow(d, y+132, y+215, (u-i*.65-.25)/.45, GREEN)
        text(d, '图示为中央队列路径；另有 send_direct 直投', 1510, 28, MUTED)
        caption = '同步事件扩展能力，异步消息连接 Agent'
    d.line((100, 1590, 980, 1590), fill='#314358', width=2)
    text(d, caption, 1665, 40)
    text(d, 'EVENT-DRIVEN AGENT CORE', 1760, 27, MUTED)
    d.rounded_rectangle((100, 1820, 980, 1826), radius=3, fill='#314358')
    d.rounded_rectangle((100, 1820, 100+880*t/15, 1826), radius=3, fill=CYAN)
    # 每个镜头轻微上移入场，节点和连线一起移动，保持端点对齐。
    if u < .3:
        shift = int(22*(1-u/.3))
        moved = Image.new('RGB', (W, H), BG)
        moved.paste(im, (0, shift))
        im = moved
    return im


def main() -> None:
    target = OUT / 'qi-agent-event-driven-15s-v2.mp4'
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
            '-pix_fmt', 'rgb24', '-s', '1080x1920', '-r', '30', '-i', '-',
            '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '17',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(target)]
    process = subprocess.Popen(args, stdin=subprocess.PIPE)
    for n in range(450):
        process.stdin.write(frame(n/FPS).tobytes())
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError('视频编码失败')
    sheet = Image.new('RGB', (1080, 1280), BG)
    for i, t in enumerate([1.5, 3.5, 5.5, 7.5, 9.5, 13.8]):
        sheet.paste(frame(t).resize((360, 640)), ((i%3)*360, (i//3)*640))
    sheet.save(OUT / '15s-storyboard-check.jpg')
    print(target, flush=True)


if __name__ == '__main__':
    main()
