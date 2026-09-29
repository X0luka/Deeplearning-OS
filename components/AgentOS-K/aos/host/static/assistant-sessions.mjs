// Remember the user's explicit conversation selection per originating material.
export const contextSessionKey = c => 'aos-assistant-context:' + JSON.stringify([c.provider,c.id]);
export function chooseContextSession(conversations, context, remembered, requested) {
  return conversations.find(c => c.id === requested && c.resources.some(r => r.provider === context.provider && r.id === context.id))
    || conversations.find(c => c.id === remembered)
    || conversations.find(c => context.legacy && c.legacy?.endsWith(':' + context.legacy))
    || conversations.find(c => c.resources.length === 1 && c.resources[0].provider === context.provider && c.resources[0].id === context.id);
}
