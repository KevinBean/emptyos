/* Optional improvisation pass shared by fiction, lyrics, and MV planning. */
(function () {
    'use strict';

    function el(tag, className, value) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (value != null) node.textContent = value;
        return node;
    }

    function field(label, placeholder) {
        var wrapper = el('label');
        wrapper.appendChild(el('span', '', label));
        var input = el('textarea');
        input.placeholder = placeholder;
        wrapper.appendChild(input);
        return {wrapper: wrapper, input: input};
    }

    function formatPath(path) {
        var lines = [];
        (path.steps || []).forEach(function (step, i) {
            lines.push((i + 1) + '. ' + step.offer + '\n   Because: ' + step.because);
        });
        if (path.return) lines.push('Return: ' + path.return);
        if (path.risk) lines.push('Watch for: ' + path.risk);
        return lines.join('\n\n');
    }

    function mount(hostId, options) {
        var host = document.getElementById(hostId);
        if (!host) return;
        if (host.firstElementChild && host.firstElementChild.classList.contains('improv-pass')) return;
        host.replaceChildren();
        var details = el('details', 'improv-pass');
        details.appendChild(el('summary', '', 'Try an improvisation pass · optional'));
        details.appendChild(el('p', '', 'Build a path from a starting point to an intended effect. Work manually or ask AI for two suggestions. Edit or discard everything; this method is still being tested.'));
        var fields = el('div', 'improv-fields');
        var start = field('Starting point', 'A line, image, scene, sound, or situation');
        var end = field('Intended ending or effect', 'What should change or be felt by the end?');
        var constraints = field('Fixed constraints (optional)', 'Character, song, style, length, budget…');
        if (options.start) start.input.value = options.start;
        if (options.end) end.input.value = options.end;
        [start, end, constraints].forEach(function (item) { fields.appendChild(item.wrapper); });
        details.appendChild(fields);
        var actions = el('div', 'improv-actions');
        var manual = el('button', '', 'Start manually');
        manual.type = 'button';
        manual.title = 'Build and edit your own chain of ideas';
        actions.appendChild(manual);
        var generate = el('button', '', 'Suggest two paths with AI');
        generate.type = 'button';
        generate.title = 'Ask your chosen model for two editable paths';
        actions.appendChild(generate);
        var modelMount = el('span', 'improv-model');
        actions.appendChild(modelMount);
        details.appendChild(actions);
        details.appendChild(el('p', '', 'AI suggestions use your configured model provider and may incur cloud cost.'));
        var status = el('div', 'improv-status');
        status.setAttribute('role', 'status');
        details.appendChild(status);
        var result = el('div', 'improv-result');
        details.appendChild(result);
        host.appendChild(details);
        if (window.EOS_UI && EOS_UI.modelPill && options.app) {
            EOS_UI.modelPill({app: options.app, domain: 'text', mount: modelMount});
        }
        details.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') details.open = false;
        });

        function addEditablePath(name, content, ai) {
            var card = el('div', 'improv-path' + (ai ? ' ai' : ''));
            if (ai) card.appendChild(el('span', 'eos-badge', '✨ suggested'));
            card.appendChild(el('h4', '', name));
            var text = el('textarea');
            text.value = content;
            text.setAttribute('aria-label', name + ' — editable');
            card.appendChild(text);
            var copy = el('button', '', 'Copy path');
            copy.type = 'button';
            copy.title = 'Copy this edited path to the clipboard';
            copy.addEventListener('click', function () {
                if (!navigator.clipboard) { status.textContent = 'Select the text to copy this path.'; return; }
                navigator.clipboard.writeText(text.value).then(function () { status.textContent = 'Path copied.'; })
                    .catch(function () { status.textContent = 'Select the text to copy this path.'; });
            });
            var row = el('div', 'improv-actions');
            row.appendChild(copy);
            card.appendChild(row);
            result.appendChild(card);
        }

        function addFeedback(mode) {
            var feedback = el('div');
            feedback.appendChild(el('p', '', 'Try a direct draft from the same starting point, then compare the two. Which helped more?'));
            var rating = el('div', 'improv-actions');
            [['better', 'More useful'], ['same', 'About the same'], ['worse', 'Less useful']].forEach(function (choice) {
                var button = el('button', '', choice[1]);
                button.type = 'button';
                button.title = 'Record your comparison: ' + choice[1].toLowerCase();
                button.addEventListener('click', async function () {
                    try {
                        var feedbackPayload = {verdict: choice[0], mode: mode};
                        if (options.medium) feedbackPayload.medium = options.medium;
                        var data = await EOS.apiSafe(options.feedback, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(feedbackPayload)});
                        if (!data.ok) throw new Error(data.error || 'Could not record comparison');
                        feedback.replaceChildren(el('p', '', 'Comparison recorded. Thank you.'));
                    } catch (error) { status.textContent = error.message; }
                });
                rating.appendChild(button);
            });
            feedback.appendChild(rating);
            result.appendChild(feedback);
        }

        manual.addEventListener('click', function () {
            if (!start.input.value.trim() || !end.input.value.trim()) {
                status.textContent = 'Add a starting point and intended effect.';
                return;
            }
            result.replaceChildren();
            addEditablePath('My path', 'Start: ' + start.input.value.trim() + '\nEnd: ' + end.input.value.trim() + '\n\n1. First offer: \n   Because: \n\n2. Consequence: \n   Because: \n\n3. Further change: \n   Because: \n\nReturn with a changed meaning: \n\nWhat might feel forced: ', false);
            addFeedback('manual');
            status.textContent = 'Follow each addition to its consequence. The text is yours to edit.';
        });

        generate.addEventListener('click', async function () {
            if (!start.input.value.trim() || !end.input.value.trim()) {
                status.textContent = 'Add a starting point and intended effect.';
                return;
            }
            generate.disabled = true;
            status.textContent = 'Exploring paths…';
            try {
                var payload = {start: start.input.value, end: end.input.value, constraints: constraints.input.value};
                if (options.medium) payload.medium = options.medium;
                if (options.context) payload.context = typeof options.context === 'function' ? options.context() : options.context;
                var data = await EOS.apiSafe(options.endpoint, {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (data.error || !Array.isArray(data.paths)) throw new Error(data.error || 'Could not explore paths');
                result.replaceChildren();
                data.paths.forEach(function (path) {
                    addEditablePath(path.name || 'Candidate path', formatPath(path), true);
                });
                if (window.EOS_UI && EOS_UI.provenanceLine) {
                    var provenance = el('div', 'improv-provenance');
                    provenance.innerHTML = EOS_UI.provenanceLine(data.provenance, {suffix: ' · Review and edit'});
                    result.appendChild(provenance);
                }
                addFeedback('ai');
                status.textContent = 'Candidate paths ready. Edit, copy, or discard them.';
            } catch (error) { status.textContent = error.message; }
            finally { generate.disabled = false; }
        });
    }

    window.EOS_IMPROV = {mount: mount};
}());
