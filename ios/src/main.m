#import "Views.h"
#import "Library.h"
#import <AVFoundation/AVFoundation.h>

@interface MusicApp : UIResponder <UIApplicationDelegate>
@property (strong, nonatomic) UIWindow *window;
@end

@implementation MusicApp
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    NSURL *support = [[NSFileManager defaultManager] URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
    NSURL *root = [support URLByAppendingPathComponent:@"Downloads" isDirectory:YES];
    Library *library = [[Library alloc] initWithDirectory:root];
    if ([library reload:NULL]) [root setResourceValue:@YES forKey:NSURLIsExcludedFromBackupKey error:NULL];
    [[AVAudioSession sharedInstance] setCategory:AVAudioSessionCategoryPlayback error:NULL];
    UINavigationController *offline = [[UINavigationController alloc] initWithRootViewController:[[DownloadsView alloc] initWithLibrary:library]];
    offline.tabBarItem = [[UITabBarItem alloc] initWithTitle:@"On My iPad" image:[UIImage imageNamed:@"Device"] tag:0];
    UINavigationController *server = [[UINavigationController alloc] initWithRootViewController:[[PlaylistsView alloc] initWithLibrary:library]];
    server.tabBarItem = [[UITabBarItem alloc] initWithTitle:@"Playlists" image:[UIImage imageNamed:@"Lists"] tag:1];
    UITabBarController *tabs = [UITabBarController new];
    tabs.viewControllers = @[offline, server];
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    self.window.tintColor = [UIColor colorWithRed:0.02 green:0.40 blue:0.39 alpha:1];
    self.window.rootViewController = tabs;
    [self.window makeKeyAndVisible];
    return YES;
}
@end

int main(int argc, char *argv[]) {
    @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass(MusicApp.class)); }
}
