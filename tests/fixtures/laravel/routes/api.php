<?php

use App\Http\Controllers\OrderController;
use Illuminate\Support\Facades\Route;

// Route::delete('/legacy', [OrderController::class, 'purge']); is a comment and must not be listed.

Route::get('/health', fn () => ['status' => 'ok']);
Route::get('/orders/{id}', [OrderController::class, 'show']);
Route::post('/orders', [OrderController::class, 'store']);

Route::middleware('auth:sanctum')->group(function () {
    Route::delete('/orders/{id}', [OrderController::class, 'destroy']);
});
