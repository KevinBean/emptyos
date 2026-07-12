import { defineView } from '/static/eos-cad-view.js';
import { mountDraftPane, teardownDraftPane } from '/static/eos-cad-views/draft-bridge.js';

const _v = defineView({
  id: 'draft-cmdbar',
  mount(vctx) { return mountDraftPane(vctx, 'draft-cmdbar'); },
  teardown() { teardownDraftPane('draft-cmdbar'); },
});

export const mount = _v.mount;
export const teardown = _v.teardown;
