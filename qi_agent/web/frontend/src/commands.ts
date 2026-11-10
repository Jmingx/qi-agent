export type CommandName =
  | '/new'
  | '/resume'
  | '/clear'
  | '/help'
  | '/delegate'
  | '/memory'
  | '/compact'
  | '/stop'
  | '/status'
  | '/theme'

export type CommandRuntime = {
  newSession: () => Promise<void>
  openSessionList: () => Promise<void>
  clearCurrentSession: () => Promise<void>
  showCommandHelp: () => void
  startDelegate: (goal: string) => Promise<void>
  openMemory: () => Promise<void>
  compact: () => Promise<void>
  stop: () => Promise<void>
  showCurrentSessionStatus: () => Promise<void>
  toggleTheme: () => void
}

export type CommandDefinition = {
  name: CommandName
  label: string
  description: string
  requiresInput?: boolean
  run: (runtime: CommandRuntime, args: string) => Promise<void> | void
}

export const COMMANDS: CommandDefinition[] = [
  { name: '/new', label: '/new', description: '新建会话', run: (runtime) => runtime.newSession() },
  { name: '/resume', label: '/resume', description: '打开会话列表', run: (runtime) => runtime.openSessionList() },
  { name: '/clear', label: '/clear', description: '清空当前上下文', run: (runtime) => runtime.clearCurrentSession() },
  { name: '/help', label: '/help', description: '显示命令帮助', run: (runtime) => runtime.showCommandHelp() },
  {
    name: '/delegate',
    label: '/delegate',
    description: '发起子任务',
    requiresInput: true,
    run: (runtime, args) => runtime.startDelegate(args),
  },
  { name: '/memory', label: '/memory', description: '打开记忆弹窗', run: (runtime) => runtime.openMemory() },
  { name: '/compact', label: '/compact', description: '压缩当前上下文', run: (runtime) => runtime.compact() },
  { name: '/stop', label: '/stop', description: '停止当前会话', run: (runtime) => runtime.stop() },
  { name: '/status', label: '/status', description: '查看会话状态', run: (runtime) => runtime.showCurrentSessionStatus() },
  { name: '/theme', label: '/theme', description: '切换主题', run: (runtime) => runtime.toggleTheme() },
]

export function getCommandDefinition(commandName: CommandName): CommandDefinition | undefined {
  return COMMANDS.find((command) => command.name === commandName)
}
