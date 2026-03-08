// Password visibility toggle function
function togglePasswordVisibility(inputId, button) {
    const passwordInput = document.getElementById(inputId);
    const eyeOpen = button.querySelector('.eye-open');
    const eyeClosed = button.querySelector('.eye-closed');
    
    if (passwordInput.type === 'password') {
        passwordInput.type = 'text';
        eyeOpen.classList.add('hidden');
        eyeClosed.classList.remove('hidden');
    } else {
        passwordInput.type = 'password';
        eyeOpen.classList.remove('hidden');
        eyeClosed.classList.add('hidden');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const bookingForm = document.getElementById('bookingForm');

    // Formatting helpers
    const cardNumber = document.getElementById('cardNumber');
    if (cardNumber) {
        cardNumber.addEventListener('input', (e) => {
            let val = e.target.value.replace(/\D/g, '');
            val = val.replace(/(.{4})/g, '$1 ').trim();
            e.target.value = val;
        });
    }

    const expiry = document.getElementById('expiry');
    if (expiry) {
        expiry.addEventListener('input', (e) => {
            let val = e.target.value.replace(/\D/g, '');
            if (val.length >= 2) {
                val = val.slice(0, 2) + '/' + val.slice(2, 4);
            }
            e.target.value = val;
        });
    }
    // Footer contact/newsletter wiring
    const supportEmail = 'odewunmimohammed@gmail.com';
    const contactLink = document.getElementById('footerContactLink');
    if (contactLink) {
        contactLink.setAttribute('href', `mailto:${supportEmail}`);
    }

    const newsletterForm = document.getElementById('newsletterForm');
    const newsletterEmail = document.getElementById('newsletterEmail');
    if (newsletterForm && newsletterEmail) {
        newsletterForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const email = (newsletterEmail.value || '').trim();
            if (!email) return;

            const subject = encodeURIComponent('CargoFish Newsletter Subscription');
            const body = encodeURIComponent(`Please add this email to the CargoFish newsletter list: ${email}`);
            window.location.href = `mailto:${supportEmail}?subject=${subject}&body=${body}`;
            newsletterEmail.value = '';
            alert('Your email app is opening to complete subscription.');
        });
    }


    // Global button loading spinner
    function ensureButtonSpinner(btn) {
        if (!btn) return;
        if (btn.querySelector('.btn-spinner')) return;
        const spinner = document.createElement('span');
        spinner.className = 'btn-spinner';
        spinner.setAttribute('aria-hidden', 'true');
        btn.appendChild(spinner);
    }

    function stopButtonLoading(btn) {
        if (!btn) return;
        btn.classList.remove('btn-loading');
        btn.removeAttribute('aria-busy');
    }

    const DEFAULT_LOADING_DURATION = 1500;

    function startButtonLoading(btn) {
        if (!btn) return;
        if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return;
        if (btn.hasAttribute('data-no-loading')) return;
        if (btn.classList.contains('btn-loading')) return;

        ensureButtonSpinner(btn);
        btn.classList.add('btn-loading');
        btn.setAttribute('aria-busy', 'true');

        const durationAttr = btn.getAttribute('data-loading-duration');
        const persistent = btn.hasAttribute('data-loading-persistent') || durationAttr === 'persist';

        let duration = DEFAULT_LOADING_DURATION;
        if (durationAttr && durationAttr !== 'persist') {
            const parsed = parseInt(durationAttr, 10);
            if (Number.isFinite(parsed)) duration = parsed;
        }

        if (!persistent && Number.isFinite(duration) && duration > 0) {
            window.setTimeout(() => stopButtonLoading(btn), duration);
        }
    }

    window.stopButtonLoading = stopButtonLoading;

    document.addEventListener('click', (event) => {
        const btn = event.target.closest('button');
        if (!btn) return;
        const type = (btn.getAttribute('type') || 'submit').toLowerCase();
        if (type == 'submit') return;
        startButtonLoading(btn);
    });

    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!form || form.hasAttribute('data-no-loading')) return;

        if (form.dataset.loadingDelaySkip == '1') {
            delete form.dataset.loadingDelaySkip;
            return;
        }

        const submitBtn = event.submitter || form.querySelector('button[type="submit"]');
        if (!submitBtn || submitBtn.hasAttribute('data-no-loading')) return;

        const delayAttr = submitBtn.getAttribute('data-loading-delay') || form.getAttribute('data-loading-delay');
        const delay = delayAttr ? parseInt(delayAttr, 10) : 0;

        submitBtn.setAttribute('data-loading-persistent', 'true');
        startButtonLoading(submitBtn);

        if (Number.isFinite(delay) && delay > 0) {
            event.preventDefault();
            window.setTimeout(() => {
                form.dataset.loadingDelaySkip = '1';
                if (form.requestSubmit) {
                    form.requestSubmit(submitBtn);
                } else {
                    form.submit();
                }
            }, delay);
        }
    }, true);
});
