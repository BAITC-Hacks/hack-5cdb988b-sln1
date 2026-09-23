var builder = DistributedApplication.CreateBuilder(args);

builder.AddProject<Projects.Extractor>("extractor");

builder.Build().Run();
