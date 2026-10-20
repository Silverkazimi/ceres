function defaultStableId(item) {
  const id = item?.txId ?? item?.id ?? item?.step_id ?? item?.stepId;
  if (id === undefined || id === null || id === '') throw new Error('A stable signing id is required.');
  return String(id);
}

function rejectedState(error) {
  return error?.cancelled || error?.name === 'AbortError' || /cancel/i.test(error?.message || '')
    ? 'cancelled' : 'rejected';
}

export function createRoutingSignatureChain({ sign, getId = defaultStableId, onStepChange = () => {} } = {}) {
  if (typeof sign !== 'function') throw new TypeError('A signing callback is required.');
  const handledIds = new Set();
  const states = new Map();
  let stopped = false;

  function change(item, id, index, state, detail = {}) {
    const update = { id, index, state, item, ...detail };
    states.set(id, update);
    // Rendering must not interrupt a wallet-signing sequence.
    try { onStepChange(update); } catch (_) {}
  }

  function observeSubmission(item, id, index, result) {
    if (!result?.submitted || typeof result.submitted.then !== 'function') return;
    Promise.resolve(result.submitted).then(
      submission => change(item, id, index, 'submitted', { result, submission }),
      error => change(item, id, index, 'submission_failed', { result, error }),
    );
  }

  return {
    get stopped() {
      return stopped;
    },
    hasHandled(id) {
      return handledIds.has(String(id));
    },
    getState(id) {
      return states.get(String(id)) || null;
    },
    snapshot() {
      return Array.from(states.values());
    },
    async run(items) {
      const results = [];
      let index = 0;

      for await (const item of items) {
        if (stopped) break;
        const id = String(getId(item));
        if (handledIds.has(id)) { index += 1; continue; }
        handledIds.add(id);
        change(item, id, index, 'signing');

        try {
          const result = await sign(item);
          results.push(result);
          change(item, id, index, 'signed', { result });
          observeSubmission(item, id, index, result);
        } catch (error) {
          stopped = true;
          if (error?.signedResult) {
            const result = error.signedResult;
            change(item, id, index, 'signed', { result, attachmentError: error });
            observeSubmission(item, id, index, result);
            throw error;
          }
          change(item, id, index, rejectedState(error), { error });
          throw error;
        }
        index += 1;
      }

      return results;
    },
  };
}
