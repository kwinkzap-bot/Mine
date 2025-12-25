#!/usr/bin/env python
"""Test script for CPR filter service to check for hanging issues."""

import logging
import time
from cpr_filter_service import CPRFilterService
from kiteconnect import KiteConnect
import os
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

def test_cpr_filter():
    """Test the CPR filter with a limited set of stocks."""
    logger.info("=" * 60)
    logger.info("CPR Filter Service Test")
    logger.info("=" * 60)
    
    try:
        # Initialize KiteConnect
        api_key = os.getenv("API_KEY")
        access_token = os.getenv("ACCESS_TOKEN")
        
        if not api_key or not access_token:
            logger.error("Missing API_KEY or ACCESS_TOKEN in .env file")
            return
        
        logger.info(f"API_KEY: {api_key[:20]}...")
        logger.info(f"ACCESS_TOKEN: {access_token[:20]}...")
        
        # Create KiteConnect instance
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        logger.info("✓ KiteConnect initialized")
        
        # Create CPRFilterService
        cpr_service = CPRFilterService(kite_instance=kite)
        logger.info("✓ CPRFilterService initialized")
        
        # Get F&O stocks
        logger.info("\nFetching F&O stocks...")
        stocks = cpr_service.get_fo_stocks()
        logger.info(f"✓ Found {len(stocks)} F&O stocks")
        
        # Test with first 5 stocks
        test_stocks = stocks[:5]
        logger.info(f"\nTesting with first {len(test_stocks)} stocks: {test_stocks}")
        
        logger.info("\nProcessing stocks...")
        start_time = time.time()
        results = []
        
        for stock in test_stocks:
            stock_start = time.time()
            try:
                result = cpr_service.process_stock(stock)
                stock_time = time.time() - stock_start
                if result:
                    results.append(result)
                    logger.info(f"✓ {stock}: {result['status']} ({stock_time:.2f}s)")
                else:
                    logger.info(f"  {stock}: Skipped (no criteria match) ({stock_time:.2f}s)")
            except Exception as e:
                stock_time = time.time() - stock_start
                logger.error(f"✗ {stock}: Error - {e} ({stock_time:.2f}s)")
        
        total_time = time.time() - start_time
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Results: {len(results)} stocks match criteria")
        logger.info(f"Total time: {total_time:.2f}s")
        logger.info(f"Avg per stock: {total_time / len(test_stocks):.2f}s")
        logger.info(f"Cache size: {len(cpr_service._historical_data_cache)} entries")
        logger.info(f"{'=' * 60}")
        
        if results:
            logger.info("\nMatching stocks:")
            for result in results:
                logger.info(f"  - {result['symbol']}: {result['status']}")
        
        return True
        
    except Exception as e:
        logger.error(f"Test failed: {type(e).__name__}: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    success = test_cpr_filter()
    exit(0 if success else 1)
