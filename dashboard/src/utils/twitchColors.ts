/** Twitch 聊天用户名色板（15 色，深浅两套）。
 *
 * 深色主题用原始色，浅色主题用对比度更高的加深色；分配沿用 Twitch 的
 * 稳定算法（用户名字符码求和取模），同一用户名在任何页面/刷新后颜色一致。
 */

const DARK = [
  '#FF0000', // Red
  '#0000FF', // Blue
  '#008000', // Green
  '#B22222', // Firebrick
  '#FF7F50', // Coral
  '#9ACD32', // YellowGreen
  '#FF4500', // OrangeRed
  '#2E8B57', // SeaGreen
  '#DAA520', // GoldenRod
  '#D2691E', // Chocolate
  '#5F9EA0', // CadetBlue
  '#1E90FF', // DodgerBlue
  '#FF69B4', // HotPink
  '#8A2BE2', // BlueViolet
  '#00FF7F', // SpringGreen
]

const LIGHT = [
  '#CC0000',
  '#0000FF',
  '#008000',
  '#B22222',
  '#D9542B',
  '#437900',
  '#DA1C00',
  '#1D7D4B',
  '#926700',
  '#B64218',
  '#337375',
  '#076DCD',
  '#C93485',
  '#8A2BE2',
  '#007B00',
]

export function colorForUsername(username: string, dark: boolean): string {
  const palette = dark ? DARK : LIGHT
  const name = username.toLowerCase()
  let sum = 0
  for (let i = 0; i < name.length; i += 1) sum += name.charCodeAt(i)
  return palette[sum % palette.length]!
}
