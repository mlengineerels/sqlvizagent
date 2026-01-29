import { ui } from "./ui.js";

export function apiRequest(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  return fetch(path, { ...options, headers }).then(async (resp) => {
    let data = null;
    try {
      data = await resp.json();
    } catch (_err) {
      data = null;
    }
    if (!resp.ok) {
      const message = (data && data.detail) || "Request failed";
      throw new Error(message);
    }
    return data;
  });
}

export function fetchQuery(
  question,
  {
    tables = [],
    planOnly = false,
    chatId = null,
    forceNew = false,
    contextMessageId = null,
  } = {}
) {
  return apiRequest("/api/query", {
    method: "POST",
    body: JSON.stringify({
      question,
      execute: !planOnly,
      plan_only: planOnly,
      force_new: forceNew,
      context_message_id: contextMessageId,
      tables,
      session_id: ui.state.sessionId,
      chat_id: chatId,
    }),
  });
}

export function sendFeedback(chatId, messageId, rating, comment = null) {
  return apiRequest("/api/feedback", {
    method: "POST",
    body: JSON.stringify({
      chat_id: chatId,
      message_id: messageId,
      rating,
      comment,
    }),
  });
}

export function fetchReplay(chatId) {
  return apiRequest(`/api/chats/${chatId}/replay`);
}
