import { type SVGProps } from 'react'

/** 统一线性图标集（内联 SVG，零依赖）。stroke 1.75 / currentColor / 24 视框。 */
export type IconName =
  | 'panel-left'
  | 'sun'
  | 'moon'
  | 'monitor'
  | 'route'
  | 'flask'
  | 'stop'
  | 'eraser'
  | 'archive'
  | 'memory'
  | 'copy'
  | 'pencil'
  | 'regenerate'
  | 'terminal'
  | 'file-text'
  | 'file-pen'
  | 'folder'
  | 'search'
  | 'globe'
  | 'braces'
  | 'git-branch'
  | 'box'
  | 'wrench'
  | 'alert'
  | 'chevron-down'
  | 'chevron-right'
  | 'plus'
  | 'close'
  | 'trash'
  | 'check'
  | 'more'
  | 'arrow-down'
  | 'send'
  | 'link'
  | 'clock'
  | 'layers'
  | 'sparkles'
  | 'quote'

type IconProps = SVGProps<SVGSVGElement> & {
  name: IconName
  size?: number
}

const PATHS: Record<IconName, JSX.Element> = {
  'panel-left': (
    <>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M9 3v18" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
    </>
  ),
  moon: <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />,
  monitor: (
    <>
      <rect x="2" y="4" width="20" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </>
  ),
  route: (
    <>
      <circle cx="6" cy="19" r="2.5" />
      <circle cx="18" cy="5" r="2.5" />
      <path d="M8.5 19H13a4 4 0 0 0 4-4V7.5" />
    </>
  ),
  flask: (
    <>
      <path d="M9 3h6M10 3v6.2L5.2 17.5A2 2 0 0 0 7 20.6h10a2 2 0 0 0 1.8-3.1L14 9.2V3" />
      <path d="M7.5 14h9" />
    </>
  ),
  stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
  eraser: (
    <>
      <path d="m13.5 4.5 6 6-8 8H6v-5.5l7.5-8.5Z" />
      <path d="M7 21h14" />
    </>
  ),
  archive: (
    <>
      <rect x="3" y="4" width="18" height="4" rx="1" />
      <path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8M10 12h4" />
    </>
  ),
  memory: (
    <>
      <rect x="7" y="7" width="10" height="10" rx="2" />
      <path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4" />
    </>
  ),
  copy: (
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V6a1 1 0 0 1 1-1h9" />
    </>
  ),
  pencil: (
    <>
      <path d="m4 20 4.5-1.2L19 8.3 15.7 5 5.2 15.5 4 20Z" />
      <path d="m13.5 7.2 3.3 3.3" />
    </>
  ),
  regenerate: (
    <>
      <path d="M20 12a8 8 0 1 1-2.4-5.7" />
      <path d="M20 4v6h-6" />
    </>
  ),
  terminal: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="m7.5 9.5 3 2.5-3 2.5M13 15h4" />
    </>
  ),
  'file-text': (
    <>
      <path d="M6 2.5h7L18.5 8v13.5H6z" />
      <path d="M13 2.5V8h5.5M9 12.5h6M9 16h6" />
    </>
  ),
  'file-pen': (
    <>
      <path d="M6 2.5h7L18.5 8v5" />
      <path d="M13 2.5V8h5.5M6 2.5v19h4" />
      <path d="m13 19 5.8-5.8 2.4 2.4L15.4 21.4 12.6 22l.4-3Z" />
    </>
  ),
  folder: <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m16.5 16.5 4 4" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.5 2.4 3.8 5.4 3.8 9S14.5 18.6 12 21c-2.5-2.4-3.8-5.4-3.8-9S9.5 5.4 12 3Z" />
    </>
  ),
  braces: (
    <>
      <path d="M9 4H8a2 2 0 0 0-2 2v3.5L4 12l2 2.5V18a2 2 0 0 0 2 2h1" />
      <path d="M15 4h1a2 2 0 0 1 2 2v3.5L20 12l-2 2.5V18a2 2 0 0 1-2 2h-1" />
    </>
  ),
  'git-branch': (
    <>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="12" r="2.5" />
      <path d="M6 8.5v7M8.5 17a6 6 0 0 0 6-5" />
    </>
  ),
  box: (
    <>
      <path d="M12 2.5 21 7v10l-9 4.5L3 17V7l9-4.5Z" />
      <path d="M3 7l9 4.5L21 7M12 11.5v10" />
    </>
  ),
  wrench: <path d="M20.5 5.5a5 5 0 0 1-6.8 6.8L6 20l-2-2 7.7-7.7A5 5 0 0 1 18.5 3.5l-3 3 2 2 3-3Z" />,
  alert: (
    <>
      <path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6A2 2 0 0 0 22 18L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4.5M12 17.5h.01" />
    </>
  ),
  'chevron-down': <path d="m6 9 6 6 6-6" />,
  'chevron-right': <path d="m9 6 6 6-6 6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  trash: (
    <>
      <path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13" />
      <path d="M10.5 11v5.5M13.5 11v5.5" />
    </>
  ),
  check: <path d="m20 6.5-11 11-5-5" />,
  more: (
    <>
      <circle cx="5" cy="12" r="1.6" />
      <circle cx="12" cy="12" r="1.6" />
      <circle cx="19" cy="12" r="1.6" />
    </>
  ),
  'arrow-down': <path d="M12 5v14M6 13l6 6 6-6" />,
  send: <path d="M21.5 2.5 11 13M21.5 2.5 15 21.5 11 13 2.5 9 21.5 2.5Z" />,
  link: (
    <>
      <path d="M10 13a5 5 0 0 1 0-7l1-1a5 5 0 0 1 7 7l-1 1" />
      <path d="M14 11a5 5 0 0 1 0 7l-1 1a5 5 0 0 1-7-7l1-1" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5.2l3.2 2" />
    </>
  ),
  layers: <path d="m12 3 9 4.5-9 4.5-9-4.5L12 3ZM3 12l9 4.5 9-4.5M3 16.5 12 21l9-4.5" />,
  sparkles: (
    <>
      <path d="M12 3.5 13.6 9 19 10.5 13.6 12 12 17.5 10.4 12 5 10.5 10.4 9 12 3.5Z" />
      <path d="M18.5 16.5l.7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7.7-2.3Z" />
    </>
  ),
  quote: <path d="M7 6.5h4v4a4.5 4.5 0 0 1-4.5 4.5H6M15 6.5h4v4a4.5 4.5 0 0 1-4.5 4.5H14" />,
}

/** 工具名 → 图标（摘要行与工具卡保持同一套语义）。 */
export const TOOL_ICONS: Record<string, IconName> = {
  shell: 'terminal',
  run_python: 'braces',
  read_file: 'file-text',
  write_file: 'file-pen',
  file_delete: 'trash',
  list_dir: 'folder',
  search_files: 'search',
  web_search: 'globe',
  web_extract: 'globe',
  delegate_task: 'git-branch',
  get_time: 'clock',
  save_memory: 'memory',
  add_memory: 'memory',
  read_memory: 'memory',
}

export function toolIcon(name: string): IconName {
  return TOOL_ICONS[name] ?? 'wrench'
}

export function Icon({ name, size = 16, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {PATHS[name]}
    </svg>
  )
}
