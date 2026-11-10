var builder = WebApplication.CreateBuilder(args);
builder.Services.AddControllers();
builder.Services.AddAuthorization(options =>
{
    options.FallbackPolicy = new AuthorizationPolicyBuilder()
        .RequireAuthenticatedUser()
        .Build();
});

var app = builder.Build();
app.UseAuthentication();
app.UseAuthorization();
app.MapControllers();
app.MapPost("/minimal/orders", (Order o) => Results.Created());
app.MapPost("/minimal/contact", (Contact c) => Results.Ok()).AllowAnonymous();
app.Run();
