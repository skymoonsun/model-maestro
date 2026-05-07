/**
 * Grafana Assistant - Backend URL Domain Validation Bypass Script
 *
 * This script bypasses the Grafana Assistant plugin's Connection page
 * validation that only accepts grafana.net domains for the Backend URL field,
 * allowing you to use any custom domain.
 *
 * Validation Mechanism:
 * The plugin checks ['.grafana.net', '.grafana-ops.net', '.grafana-dev.net']
 * using Array.prototype.some() to verify the hostname ends with one of these.
 * This script temporarily overrides some() to bypass that check.
 *
 * Usage:
 * 1. Go to http://<grafana-host>:3000/plugins/grafana-assistant-app
 * 2. Click on the Connection tab
 * 3. Open Browser DevTools Console (F12 > Console)
 * 4. Paste this script and press Enter
 * 5. The script will auto-expand Manual configuration if needed,
 *    fill all fields, bypass validation, and save
 */

(function grafanaAssistantBypass() {
    'use strict';

    // --- Configuration ---
    const CONFIG = {
        backendUrl: 'http://localhost:8000/grafana/assistant',
        instanceId: '1622805',
        apiToken: 'API_KEY'
    };

    // Get React's native value setter (needed to update React controlled inputs)
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    ).set;

    /**
     * Updates the value of a React controlled input element
     */
    function setReactInputValue(input, value) {
        nativeInputValueSetter.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    /**
     * Expands the "Manual configuration" collapsible section if it's collapsed.
     */
    function ensureManualConfigOpen() {
        if (document.querySelector('form')) {
            console.log('[Bypass] Manual configuration section is already open.');
            return true;
        }

        const headerLabels = document.querySelectorAll('[class*="collapse__header-label"]');
        for (const label of headerLabels) {
            if (label.textContent.includes('Manual configuration')) {
                const headerDiv = label.parentElement;
                if (headerDiv) {
                    console.log('[Bypass] Found collapse header label, clicking header wrapper...');
                    headerDiv.click();
                    return true;
                }
            }
        }

        const collapseButtons = document.querySelectorAll('button[aria-expanded="false"]');
        for (const btn of collapseButtons) {
            const parent = btn.parentElement;
            if (parent && parent.textContent.includes('Manual configuration')) {
                console.log('[Bypass] Found collapse button via aria-expanded, clicking parent header...');
                parent.click();
                return true;
            }
        }

        const collapseHeaders = document.querySelectorAll('[class*="collapse__header"]');
        for (const header of collapseHeaders) {
            if (header.textContent.includes('Manual configuration')) {
                console.log('[Bypass] Found collapse header via class pattern, clicking...');
                header.click();
                return true;
            }
        }

        const allButtons = document.querySelectorAll('button[aria-expanded]');
        for (const btn of allButtons) {
            const sibling = btn.nextElementSibling;
            if (sibling && sibling.textContent.includes('Manual configuration')) {
                console.log('[Bypass] Found toggle button next to Manual configuration label, clicking...');
                btn.click();
                return true;
            }
        }

        console.error('[Bypass] Could not find the Manual configuration toggle!');
        return false;
    }

    /**
     * Finds and fills the form input fields with configured values
     */
    function fillFormFields() {
        const form = document.querySelector('form');
        if (!form) {
            console.error('[Bypass] Form not found even after expanding the section!');
            return false;
        }

        const inputs = form.querySelectorAll('input');
        if (inputs.length < 3) {
            console.error('[Bypass] Not enough input fields found. Is the page fully loaded?');
            return false;
        }

        const [backendUrlInput, instanceIdInput, apiTokenInput] = inputs;

        console.log('[Bypass] Setting Backend URL:', CONFIG.backendUrl);
        setReactInputValue(backendUrlInput, CONFIG.backendUrl);

        console.log('[Bypass] Setting Instance ID:', CONFIG.instanceId);
        setReactInputValue(instanceIdInput, CONFIG.instanceId);

        console.log('[Bypass] Setting API Token...');
        setReactInputValue(apiTokenInput, CONFIG.apiToken);

        return true;
    }

    /**
     * Overrides Array.prototype.some to bypass the grafana.net domain validation,
     * then enables and clicks the Save & connect button
     */
    function bypassValidationAndSave() {
        const buttons = document.querySelectorAll('button');
        const saveBtn = Array.from(buttons).find(b =>
            b.textContent.toLowerCase().includes('save')
        );

        if (!saveBtn) {
            console.error('[Bypass] Save & connect button not found!');
            return false;
        }

        saveBtn.disabled = false;
        saveBtn.removeAttribute('aria-disabled');
        saveBtn.style.pointerEvents = 'auto';
        saveBtn.style.opacity = '1';

        const originalSome = Array.prototype.some;
        Array.prototype.some = function(...args) {
            if (this.length > 0 && typeof this[0] === 'string' && this[0].includes('grafana')) {
                console.log('[Bypass] Domain validation bypassed!');
                return true;
            }
            return originalSome.apply(this, args);
        };

        console.log('[Bypass] Clicking Save & connect...');
        saveBtn.click();

        setTimeout(() => {
            Array.prototype.some = originalSome;
            console.log('[Bypass] Array.prototype.some restored to original.');
        }, 100);

        return true;
    }

    /**
     * Main execution flow with polling for form appearance
     */
    function executeBypass() {
        let attempts = 0;
        const maxAttempts = 30;

        const waitForForm = setInterval(() => {
            attempts++;
            const form = document.querySelector('form');

            if (form) {
                clearInterval(waitForForm);
                console.log('[Bypass] Form found after ' + attempts + ' attempts, filling fields...');

                if (fillFormFields()) {
                    setTimeout(() => {
                        console.log('[Bypass] Bypassing validation and saving...');
                        if (bypassValidationAndSave()) {
                            console.log('[Bypass] Done! Waiting for server response...');
                        }
                    }, 500);
                }
            } else if (attempts >= maxAttempts) {
                clearInterval(waitForForm);
                console.error('[Bypass] Timed out waiting for the form to appear.');
                console.error('[Bypass] Please manually click "Manual configuration" to expand it, then run this script again.');
            }
        }, 300);
    }

    console.log('=== Grafana Assistant Bypass Script ===');
    console.log('[Bypass] Checking Manual configuration section...');
    const opened = ensureManualConfigOpen();

    if (opened) {
        console.log('[Bypass] Manual configuration toggle clicked, waiting for form to render...');
    }

    executeBypass();
})();
