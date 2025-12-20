function showNotification(message, type = 'info') {
    const container = document.getElementById(CONSTANTS.DOM_IDS.NOTIFICATION_CONTAINER);
    if (!container) {
        console.error('Notification container not found!');
        return;
    }

    const notification = document.createElement('div');
    notification.className = `${CONSTANTS.CSS_CLASSES.NOTIFICATION} ${type}`;
    notification.textContent = message;

    container.appendChild(notification);

    // Automatically remove after notification timeout
    setTimeout(() => {
        // Ensure it's still in the container before removing
        if (container.contains(notification)) {
            notification.remove();
        }
    }, CONSTANTS.TIMEOUTS.NOTIFICATION_DURATION);
}