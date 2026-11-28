import { createHash } from 'node:crypto';

export function questionRef(session, callId) {
  return createHash('sha256').update(session).update('\0').update(callId).digest('hex').slice(0, 8);
}

export function questionAnswers(questions, text) {
  const values = new Map();
  if (questions.length === 1) {
    values.set(1, text.trim());
  } else {
    for (const line of text.trim().split(/\r?\n/)) {
      const match = line.match(/^\s*(\d+)\s*=\s*(.+?)\s*$/);
      const index = Number(match?.[1]);
      if (!match || index < 1 || index > questions.length || values.has(index)) return null;
      values.set(index, match[2]);
    }
  }
  if (values.size !== questions.length) return null;
  const answers = [];
  for (const [index, question] of questions.entries()) {
    const value = values.get(index + 1);
    if (!value) return null;
    const options = (question.options || []).map(option => option.label);
    if (options.length && /^\d+(?:\s*[,，、]\s*\d+)*$/.test(value)) {
      const numbers = value.split(/[,，、]/).map(Number);
      if (numbers.some(number => number < 1 || number > options.length)
          || new Set(numbers).size !== numbers.length || (!question.multiSelect && numbers.length > 1)) return null;
      answers.push({ id: question.id, selected: numbers.map(number => options[number - 1]) });
    } else if (options.includes(value)) {
      answers.push({ id: question.id, selected: [value] });
    } else {
      answers.push({ id: question.id, selected: [], custom: value });
    }
  }
  return { answers };
}
