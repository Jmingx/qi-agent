"""新手向核心循环示意视频：用固定坐标与逐帧路径保证连线准确。"""
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
BG, WHITE, GRAY = '#0C1625', '#F3F7FF', '#ACBCD0'
CYAN, GOLD, GREEN = '#68DAF0', '#FFD28A', '#82E0B6'
FONT = {}
YS = [470, 645, 820, 995, 1170]
NAMES = ['整理要发给模型的信息', '请求大模型', '判断是否要调用工具', '执行工具', '把工具结果写回消息']
SUBS = ['问题 + 历史 + 插件补充', '模型决定：用工具，还是回答', '有工具请求时，先做安全检查', '本例：读取 notes.txt', 'role=tool，供下一次模型读取']
STEPS = [0, 2, 5, 8, 11, 14, 17, 20]


def f(size: int) -> ImageFont.FreeTypeFont:
    if size not in FONT:
        FONT[size] = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', size)
    return FONT[size]


def txt(d: ImageDraw.ImageDraw, s: str, x: float, y: float,
        size: int = 32, color: str = WHITE, anchor: str = 'mm') -> None:
    d.text((x, y), s, font=f(size), fill=color, anchor=anchor)


def path(d: ImageDraw.ImageDraw, points: list, progress: float = -1,
         color: str = CYAN) -> None:
    d.line(points, fill='#53677F', width=4, joint='curve')
    a, b = points[-2:]
    angle = math.atan2(b[1]-a[1], b[0]-a[0])
    d.polygon([b, (b[0]-17*math.cos(angle-.45), b[1]-17*math.sin(angle-.45)),
               (b[0]-17*math.cos(angle+.45), b[1]-17*math.sin(angle+.45))], fill=color)
    if not 0 <= progress <= 1:
        return
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    remaining = progress*sum(lengths)
    for (a, b), length in zip(zip(points, points[1:]), lengths):
        if remaining <= length:
            ratio = remaining/length if length else 0
            x, y = a[0]+(b[0]-a[0])*ratio, a[1]+(b[1]-a[1])*ratio
            d.ellipse((x-15,y-15,x+15,y+15), fill=color)
            break
        remaining -= length


def plugin(d: ImageDraw.ImageDraw, y: int, title: str, lines: list,
           p: float, color: str = GOLD, start: bool = False) -> None:
    # 插件横线与主流程纵线分区，端点停在框边，不穿过文字。
    points = [(840,345),(840,440),(990,440),(990,y+57),(975,y+57)] if start else [(610,y+57),(700,y+57)]
    path(d, points, min(1,p), color)
    d.rounded_rectangle((700,y-10,975,y+143), radius=18, fill='#253047', outline=color, width=2)
    txt(d,title,837,y+24,31,color)
    for i,line in enumerate(lines):
        txt(d,line,837,y+69+i*37,26)


def frame(t: float) -> Image.Image:
    im = Image.new('RGB',(1080,1920),BG)
    d = ImageDraw.Draw(im)
    scene = next(i for i in range(7) if t < STEPS[i+1])
    u = t-STEPS[scene]
    headings = ['一句话，Agent 怎么完成？','先给模型准备好资料','模型先提出工具请求','工具不能想用就用','工具结果，要带回循环','带着新结果，再问一次模型','这次无需工具，输出答案']
    txt(d,'qi-agent  /  核心循环事件驱动模型',540,83,31,CYAN)
    txt(d,headings[scene],540,172,48)
    d.rounded_rectangle((85,242,995,345),radius=20,fill='#1A2B41')
    question = '用户：请总结 notes.txt'
    shown = question[:max(1,int(t*18))] if t < 2 else question
    txt(d,shown,540,292,39)
    txt(d,'● 主流程',190,398,27,CYAN)
    txt(d,'事件 → 插件响应',810,398,27,GOLD)
    active = [None,0,1,2,3,4,None][scene]
    if scene == 4 and u > 1.7:
        active = 4
    if scene == 5 and u > 1.25:
        active = 0 if u < 1.8 else 1 if u < 2.5 else 2
    if scene == 6:
        active = 2 if u < .75 else 5
    # 主流程常驻，学习者无需在镜头切换时重新寻找位置。
    for i,y in enumerate(YS):
        if i < 4:
            p = -1
            if (scene == 2 and i == 0) or (scene == 3 and i == 1) or (scene == 4 and i in (2,3)):
                p = (u-(1.5 if i==3 else 0))/.7
            path(d,[(365,y+114),(365,y+175)],p)
        on = active == i
        d.rounded_rectangle((125,y,610,y+114),radius=18,fill='#1B3545' if on else '#142338',outline=CYAN if on else '#3A4F68',width=3)
        txt(d,str(i+1),151,y+29,24,CYAN)
        txt(d,NAMES[i],367,y+42,32,CYAN if on else WHITE)
        txt(d,SUBS[i],367,y+86,23,GRAY)
    # 回边只从结果回填出发，并回到 pre-step，而不是直接跳入模型。
    route = [(125,1227),(61,1227),(61,527),(125,527)]
    path(d,route,(u/.95) if scene==5 else -1,GREEN)
    txt(d,'下一次循环',275,1315,26,GREEN)
    txt(d,'有工具请求 ↓',475,968,22,GRAY)
    d.rounded_rectangle((125,1370,975,1465),radius=18,fill='#16382F' if active==5 else '#142338',outline=GREEN if active==5 else '#3A4F68',width=3)
    txt(d,'最终答案',550,1418,35,GREEN)
    if scene == 0:
        plugin(d,470,'开始一轮对话',['turn-start','日志记录用户输入'],u/.8,start=True)
    elif scene == 1:
        plugin(d,470,'准备上下文',['pre-step','插件补充、裁剪信息'],u/.6)
        txt(d,'waterfall：改完再传下一个',808,681,24,GOLD)
        txt(d,'插件返回新消息列表',808,735,26,GRAY)
    elif scene == 2:
        plugin(d,645,'模型调用前 / 后',['pre-llm / post-llm','日志、耗时、用量'],u/.7)
        txt(d,'模型返回工具请求',810,910,28,GOLD)
        txt(d,'read_file',810,960,34)
        txt(d,'notes.txt',810,1007,28,GRAY)
    elif scene == 3:
        plugin(d,820,'安全检查插件',['tool-call · bail','本例：检查后放行'],u/.6)
        txt(d,'若需审批 → tool-approval',804,1045,22,GOLD)
        txt(d,'拒绝时不会执行工具',812,1093,25,GRAY)
    elif scene == 4:
        plugin(d,995,'工具开始 / 结果',['tool/start','agent/tool-result'],u/.6)
        txt(d,'日志与统计插件记录结果',810,1210,23,GOLD)
        # 文件读取条随时间增长，表现实际执行而不伪造实测数据。
        for k in range(3):
            width = 175*min(1,max(0,(u-k*.35)/.45))
            d.rounded_rectangle((727,1270+k*20,727+max(2,width),1277+k*20),radius=3,fill=GREEN)
    elif scene == 5:
        plugin(d,470,'再次准备上下文',['pre-step','这次多了工具结果'],max(0,(u-.9)/.5))
        txt(d,'再次请求模型',812,799,33,GREEN)
        txt(d,'pre-llm → post-llm',812,854,24,GRAY)
        txt(d,'模型现在有依据了',812,941,29)
    else:
        path(d,[(610,877),(1020,877),(1020,1417),(975,1417)],min(1,u/.9),GREEN)
        txt(d,'无工具请求',821,824,31,GREEN)
        txt(d,'final-answer',809,1003,32,GOLD)
        txt(d,'通知日志等监听器',813,1058,25,GRAY)
    # 示例消息卡让“回填”成为可见的数据变化。
    d.rounded_rectangle((85,1515,995,1740),radius=20,fill='#18283E')
    detail = [('问题进入消息历史','事件就像流程中的“通知站”','到站后，订阅它的插件开始工作。'),
              ('问题 + 历史 + 上下文','pre-step 是可改写的事件点','补充资料后，才把消息交给模型。'),
              ('模型：“我需要先读文件”','模型只提出请求','真正执行工具的是 Agent 的执行器。'),
              ('安全插件：“这个请求可以执行”','bail：首个非 None 返回就停止分发','调用方根据决策放行、拒绝或要求审批。'),
              ('示例文件内容：周五发布，测试待完成','工具结果 → 消息历史','结果回填后，循环还要继续。'),
              ('新的输入 = 原问题 + 文件内容','回到准备阶段，再请求模型','模型依据工具结果生成回答。'),
              ('答案：周五发布，发布前需完成测试','核心推进流程，事件连接插件','示例仅演示正常路径；非真实运行记录。')][scene]
    txt(d,detail[0],540,1565,32,WHITE)
    txt(d,detail[1],540,1635,29,CYAN)
    txt(d,detail[2],540,1695,26,GRAY)
    txt(d,'事件监听器同步、按序执行  ·  事件名除 tool/start 外省略 agent/',540,1794,23,GRAY)
    d.rounded_rectangle((90,1842,990,1848),radius=3,fill='#35465B')
    d.rounded_rectangle((90,1842,90+900*t/20,1848),radius=3,fill=CYAN)
    return im


def main() -> None:
    out = OUT/'qi-agent-core-loop-beginner-20s.mp4'
    process = subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1080x1920','-r','30','-i','-','-an','-c:v','libx264','-crf','17','-preset','fast','-pix_fmt','yuv420p','-movflags','+faststart',str(out)],stdin=subprocess.PIPE)
    for n in range(600):
        process.stdin.write(frame(n/30).tobytes())
    process.stdin.close()
    if process.wait():
        raise RuntimeError('编码失败')
    sheet = Image.new('RGB',(1440,1280),BG)
    for i,t in enumerate([1.5,3.8,6.8,9.8,12.8,14.5,16.5,19]):
        sheet.paste(frame(t).resize((360,640)),((i%4)*360,(i//4)*640))
    sheet.save(OUT/'core-loop-20s-check.jpg')
    print(out)


if __name__ == '__main__':
    main()
