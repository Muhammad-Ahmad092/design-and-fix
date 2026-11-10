<?php

use App\Http\Controllers\Api\NoteController;
use Illuminate\Support\Facades\Route;

Route::group(['namespace' => 'Api'], function () {
    Route::post('users/login', 'AuthController@login');
    Route::post('users', 'AuthController@register');

    Route::get('profiles/{user}', 'ProfileController@show');
    Route::post('profiles/{user}/follow', 'ProfileController@follow');
    Route::delete('profiles/{user}/follow', 'ProfileController@unFollow');

    Route::resource('articles', 'ArticleController', [
        'except' => ['create', 'edit']
    ]);

    Route::post('notes', [NoteController::class, 'store']);
});
