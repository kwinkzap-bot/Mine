function showNotification(message, type = 'info') {
    const container = document.getElementById('notification-container');
    if (!container) {
        console.error('Notification container not found!');
        return;
    }

    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;

    container.appendChild(notification);

    // Automatically remove after 5 seconds
    setTimeout(() => {
        // Ensure it's still in the container before removing
        if (container.contains(notification)) {
            notification.remove();
        }
    }, 5000);
}