"use strict";

function createEventProtocol(handlers) {
  return function handleEvent(message) {
    const { type, payload } = message;
    const handler = handlers[type];
    if (handler) handler(payload);
  };
}
