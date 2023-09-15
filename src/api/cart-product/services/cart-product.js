'use strict';

/**
 * cart-product service
 */

const { createCoreService } = require('@strapi/strapi').factories;

module.exports = createCoreService('api::cart-product.cart-product');
