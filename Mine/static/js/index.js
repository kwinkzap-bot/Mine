/**
 * index.js - Pure Vanilla JavaScript for the home page.
 * Handles the click events for the Login and Refresh buttons.
 * It uses attribute selectors to maintain compatibility with the original HTML structure.
 */
document.addEventListener('DOMContentLoaded', function() {
    'use strict';

    const statusDiv = document.getElementById('status');
    // Target the buttons by their original ng-click attribute for robustness
    const loginBtn = document.querySelector('[ng-click="vm.loginZerodha()"]'); 
    const refreshBtn = document.querySelector('[ng-click="vm.refresh()"]'); 

    if (statusDiv) {
        // Initialize the status message, replacing the Angular variable if present.
        if (statusDiv.textContent.includes('vm.message')) {
            statusDiv.textContent = 'Welcome to the Stock Scanner!';
        }
        // Remove angular controller attribute from the container
        const container = document.querySelector('[ng-controller="IndexController as vm"]');
        if (container) {
             container.removeAttribute('ng-controller');
        }
    }

    if (loginBtn) {
        // Replace ng-click with vanilla JS event listener and clean up attribute
        loginBtn.removeAttribute('ng-click');
        loginBtn.addEventListener('click', function() {
            window.location.href = '/auth/login';
        });
    }

    if (refreshBtn) {
        // Replace ng-click with vanilla JS event listener and clean up attribute
        refreshBtn.removeAttribute('ng-click');
        refreshBtn.addEventListener('click', function() {
            window.location.reload();
        });
    }
});