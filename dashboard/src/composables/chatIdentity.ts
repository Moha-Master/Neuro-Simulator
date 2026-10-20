import { readonly, ref } from 'vue'

/** 聊天栏默认身份：Vedal 本人在自己直播间里也确实经常直接发消息。 */
export const DEFAULT_CHAT_USERNAME = 'vedal987'

const username = ref(DEFAULT_CHAT_USERNAME)

/** 临时改聊天身份（只作用于本次页面会话，刷新后回到默认值）。 */
export function setChatUsername(name?: string | null): string {
  username.value = (name ?? '').trim() || DEFAULT_CHAT_USERNAME
  return username.value
}

export function useChatIdentity() {
  return {
    username: readonly(username),
    setUsername: setChatUsername,
  }
}
