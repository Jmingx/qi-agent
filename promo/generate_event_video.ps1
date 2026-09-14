$out = "C:\Users\xie\PycharmProjects\qi-agent\promo\qi-agent-event-driven-20s.mp4"
$font = "C\:/Windows/Fonts/msyh.ttc"
$vf = @"
drawbox=x=0:y=0:w=1080:h=1920:color=#07101f@1:t=fill,
drawgrid=w=72:h=72:t=1:c=#18304a@0.32,
drawtext=fontfile='$font':text='QI-AGENT  /  CORE DESIGN':fontcolor=#65e6ff:fontsize=26:x=72:y=92,
drawtext=fontfile='$font':text='FROM ONE BIG LOOP':fontcolor=white:fontsize=58:x=72:y=155:enable='between(t,0,6)',
drawtext=fontfile='$font':text='TO AN EXTENSIBLE CORE':fontcolor=white:fontsize=48:x=72:y=230:enable='between(t,0,6)',
drawtext=fontfile='$font':text='MORE FEATURES = MORE CHAOS':fontcolor=#a6b8d2:fontsize=31:x=72:y=1660:enable=0,
drawtext=fontfile='$font':text='THE LOOP ONLY MOVES STATE':fontcolor=#a6b8d2:fontsize=31:x=72:y=1660:enable=0,
drawtext=fontfile='$font':text='KEY NODES EMIT EVENTS':fontcolor=#a6b8d2:fontsize=31:x=72:y=1660:enable=0,
drawtext=fontfile='$font':text='EVENTBUS ROUTES TO PLUGINS':fontcolor=#a6b8d2:fontsize=31:x=72:y=1660:enable=0,
drawtext=fontfile='$font':text='MAILBOX - ASYNC AGENT MESSAGES':fontcolor=#a6b8d2:fontsize=31:x=72:y=1660:enable=0,
drawtext=fontfile='$font':text='SYNC EVENTS EXTEND. ASYNC MESSAGES COLLABORATE.':fontcolor=#66e3a2:fontsize=24:x=72:y=1660:enable=0,
drawbox=x=540:y=440:w=2:h=1030:color=#65e6ff@0.72:t=fill,
drawbox=x=460:y=390:w=160:h=92:color=#10243c@1:t=fill,
drawtext=fontfile='$font':text='Agent Loop':fontcolor=white:fontsize=31:x=475:y=414,
drawtext=fontfile='$font':text='STATE PROGRESSION':fontcolor=#8ea4c5:fontsize=18:x=482:y=455,
drawbox=x=390:y=760:w=300:h=76:color=#241d48@1:t=fill,
drawtext=fontfile='$font':text='EventBus  /  EVENT BUS':fontcolor=#b68cff:fontsize=25:x=414:y=784,
drawbox=x=540:y=1030:w=230:h=2:color=#b68cff@0.72:t=fill,
drawbox=x=540:y=1180:w=230:h=2:color=#b68cff@0.72:t=fill,
drawbox=x=310:y=1320:w=230:h=2:color=#b68cff@0.72:t=fill,
drawbox=x=770:y=1320:w=230:h=2:color=#b68cff@0.72:t=fill,
drawbox=x=770:y=985:w=220:h=90:color=#101d33@1:t=fill,
drawbox=x=770:y=1135:w=220:h=90:color=#101d33@1:t=fill,
drawbox=x=90:y=1275:w=220:h=90:color=#101d33@1:t=fill,
drawbox=x=770:y=1275:w=220:h=90:color=#101d33@1:t=fill,
drawtext=fontfile='$font':text='SECURITY GATE':fontcolor=white:fontsize=24:x=805:y=1005,
drawtext=fontfile='$font':text='bail · BLOCK':fontcolor=#ffb86b:fontsize=19:x=810:y=1042,
drawtext=fontfile='$font':text='CONTEXT MEMORY':fontcolor=white:fontsize=22:x=790:y=1155,
drawtext=fontfile='$font':text='waterfall · REWRITE':fontcolor=#65e6ff:fontsize=18:x=782:y=1192,
drawtext=fontfile='$font':text='LOGS / OTEL':fontcolor=white:fontsize=24:x=115:y=1295,
drawtext=fontfile='$font':text='emit · BROADCAST':fontcolor=#66e3a2:fontsize=19:x=122:y=1332,
drawtext=fontfile='$font':text='STATS PLUGIN':fontcolor=white:fontsize=24:x=815:y=1295,
drawtext=fontfile='$font':text='OBSERVABILITY':fontcolor=#b68cff:fontsize=18:x=815:y=1332,
drawtext=fontfile='$font':text='turn-start  →  pre-step  →  tool-call  →  final-answer':fontcolor=#8ea4c5:fontsize=20:x=150:y=1510:enable='between(t,6,14)',
drawtext=fontfile='$font':text='EVENTBUS  !=  MAILBOX':fontcolor=#65e6ff:fontsize=35:x=72:y=300:enable='between(t,14,17)',
drawtext=fontfile='$font':text='qi-agent':fontcolor=white:fontsize=72:x=72:y=370:enable='between(t,17,20)',
drawtext=fontfile='$font':text='Event-driven Agent Core':fontcolor=#65e6ff:fontsize=30:x=78:y=455:enable='between(t,17,20)',
drawbox=x=531:y='440+((min(max(t-3,0),14))/14)*1030':w=20:h=20:color=#65e6ff@1:t=fill,
drawbox=x=531:y=760:w=20:h=20:color=#b68cff@1:t=fill:enable='between(t,6,10)',
drawbox=x=531:y=1180:w=20:h=20:color=#66e3a2@1:t=fill:enable='between(t,10,14)'
"@
$vf = $vf.Replace('#', '0x')
$vf = $vf -replace "`r?`n", ""
$vf = $vf.Replace("'", "")
$vf = $vf.Replace("fontfile=C\:/Windows/Fonts/msyh.ttc", "fontfile='C\:/Windows/Fonts/msyh.ttc'")
$vf = $vf -replace "between\(t,([0-9]+),([0-9]+)\)", "between(t\,`$1\,`$2)"
$vf = $vf.Replace("y=440+((min(max(t-3,0),14))/14)*1030", "y=780")
$filterFile = "C:\Users\xie\PycharmProjects\qi-agent\promo\event-video-filter.txt"
[IO.File]::WriteAllText($filterFile, "$vf[v];[1:a]volume=0.025[a1];[2:a]volume=0.015[a2];[a1][a2]amix=inputs=2:duration=longest,afade=t=in:st=0:d=1,afade=t=out:st=18:d=2[a]", [Text.UTF8Encoding]::new($false))
ffmpeg -y -f lavfi -i "color=c=0x07101f:s=1080x1920:r=30:d=20" -f lavfi -i "sine=frequency=220:sample_rate=48000:duration=20" -f lavfi -i "sine=frequency=330:sample_rate=48000:duration=20" -filter_complex "$vf[v];[1:a]volume=0.025[a1];[2:a]volume=0.015[a2];[a1][a2]amix=inputs=2:duration=longest,afade=t=in:st=0:d=1,afade=t=out:st=18:d=2[a]" -map "[v]" -map "[a]" -c:v libx264 -pix_fmt yuv420p -preset medium -crf 18 -c:a aac -b:a 128k -shortest $out
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output $out
