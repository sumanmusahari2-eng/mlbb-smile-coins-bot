// Minimal Telegram Bot API client over fetch. Workers reach
// api.telegram.org directly — no proxy, so sends are fast; one immediate
// retry covers transient failures (no long backoff sleeps on Workers).

export async function tg(env, method, payload) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function sendMessage(env, chatId, text, replyMarkup) {
  const payload = { chat_id: chatId, text, parse_mode: "HTML" };
  if (replyMarkup) payload.reply_markup = replyMarkup;
  try {
    return await tg(env, "sendMessage", payload);
  } catch (e) {
    return tg(env, "sendMessage", payload);
  }
}

export async function answerCallbackQuery(env, callbackQueryId) {
  try {
    return await tg(env, "answerCallbackQuery", { callback_query_id: callbackQueryId });
  } catch { return null; }
}

// Menu button commands. Telegram shows ONE list to everyone by default,
// so /admin stays OUT of the default list; admins get their own
// per-chat menu (BotCommandScopeChat) that includes it.
export async function setCommands(env, adminIds) {
  const def = [
    { command: "start", description: "Welcome + balances" },
    { command: "getid", description: "Your account ID" },
    { command: "packs", description: "View all MLBB packs" },
    { command: "orders", description: "List your orders" },
    { command: "balance", description: "Per-region balances" },
    { command: "br", description: "Quick order BR: /br uid zone diamonds" },
    { command: "ph", description: "Quick order PH: /ph uid zone diamonds" },
    { command: "help", description: "All commands" },
  ];
  const results = [];
  try {
    results.push(await tg(env, "setMyCommands", { commands: def }));
  } catch (e) {
    results.push({ ok: false, error: String(e) });
  }
  const scoped = [...def, { command: "admin", description: "Admin panel" }];
  for (const adminId of [...adminIds].sort((a, b) => a - b)) {
    try {
      results.push(await tg(env, "setMyCommands", {
        commands: scoped,
        scope: { type: "chat", chat_id: adminId },
      }));
    } catch (e) {
      results.push({ ok: false, error: String(e) });
    }
  }
  return results;
}
